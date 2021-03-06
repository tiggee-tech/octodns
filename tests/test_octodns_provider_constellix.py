#
#
#


from __future__ import absolute_import, division, print_function, \
    unicode_literals

from mock import Mock, call
from os.path import dirname, join
from requests import HTTPError, Response
from requests_mock import ANY, mock as requests_mock
from six import text_type
from unittest import TestCase

from octodns.record import Record
from octodns.provider.constellix import ConstellixClientNotFound, \
    ConstellixProvider
from octodns.provider.yaml import YamlProvider
from octodns.zone import Zone

import json


class TestConstellixProvider(TestCase):
    expected = Zone('unit.tests.', [])
    source = YamlProvider('test', join(dirname(__file__), 'config'))
    source.populate(expected)

    # Our test suite differs a bit, add our NS and remove the simple one
    expected.add_record(Record.new(expected, 'under', {
        'ttl': 3600,
        'type': 'NS',
        'values': [
            'ns1.unit.tests.',
            'ns2.unit.tests.',
        ]
    }))

    # Add some ALIAS records
    expected.add_record(Record.new(expected, '', {
        'ttl': 1800,
        'type': 'ALIAS',
        'value': 'aname.unit.tests.'
    }))

    expected.add_record(Record.new(expected, 'sub', {
        'ttl': 1800,
        'type': 'ALIAS',
        'value': 'aname.unit.tests.'
    }))

    for record in list(expected.records):
        if record.name == 'sub' and record._type == 'NS':
            expected._remove_record(record)
            break

    @staticmethod
    def _fixture(filename):
        return join(join(dirname(__file__), 'fixtures'), filename)

    @staticmethod
    def json_func(json_element):
        def json_inner_func():
            return json_element
        return json_inner_func

    def test_populate(self):
        provider = ConstellixProvider(
            'test', 'api', 'secret', ratelimit_delay=0.2
        )

        # Bad auth
        with requests_mock() as mock:
            mock.get(ANY,
                     status_code=401,
                     text='{"error": ["API key not found"]}'
                     )

            with self.assertRaises(Exception) as ctx:
                zone = Zone('unit.tests.', [])
                provider.populate(zone)
            self.assertEquals('Unauthorized', text_type(ctx.exception))

        # Bad request
        with requests_mock() as mock:
            mock.get(ANY,
                     status_code=400,
                     text='{"error": ["Rate limit exceeded"]}'
                     )

            with self.assertRaises(Exception) as ctx:
                zone = Zone('unit.tests.', [])
                provider.populate(zone)
            self.assertEquals('\n  - "unittests" is not a valid domain name',
                              text_type(ctx.exception))

        # General error
        with requests_mock() as mock:
            mock.get(ANY,
                     status_code=502,
                     text='Things caught fire'
                     )

            with self.assertRaises(HTTPError) as ctx:
                zone = Zone('unit.tests.', [])
                provider.populate(zone)
            self.assertEquals(502, ctx.exception.response.status_code)

        # Non-existent zone doesn't populate anything
        with requests_mock() as mock:
            mock.get(ANY,
                     status_code=404,
                     text='<html><head></head><body></body></html>'
                     )

            zone = Zone('unit.tests.', [])
            provider.populate(zone)
            self.assertEquals(set(), zone.records)

        # No diffs == no changes
        with requests_mock() as mock:
            base = 'https://api.dns.constellix.com/v1'
            with open(self._fixture('constellix-domains.json')) as fh:
                mock.get('{}{}'.format(base, '/domains'), text=fh.read())
            with open(self._fixture('constellix-records.json')) as fh:
                mock.get(
                    '{}{}'.format(base, '/domains/123123/records'),
                    text=fh.read()
                )

                zone = Zone('unit.tests.', [])
                provider.populate(zone)
                self.assertEquals(22, len(zone.records))
                changes = self.expected.changes(zone, provider)
                self.assertEquals(26, len(changes))

        # 2nd populate makes no network calls/all from cache
        again = Zone('unit.tests.', [])
        provider.populate(again)
        self.assertEquals(22, len(again.records))

        # bust the cache
        del provider._zone_records[zone.name]

    def test_apply(self):
        # Create provider with sandbox enabled
        provider = ConstellixProvider('test', 'api', 'secret', True)

        resp = Mock()
        resp.json = Mock()
        provider._client._request = Mock(return_value=resp)

        with open(self._fixture('constellix-domains.json')) as fh:
            domains = json.load(fh)

        # non-existent domain, create everything
        resp.json.side_effect = [
            ConstellixClientNotFound,  # no zone in populate
            ConstellixClientNotFound,  # no domain during apply
            domains
        ]
        plan = provider.plan(self.expected)

        # No root NS, no ignored, no excluded, no unsupported
        n = len(self.expected.records) - 4
        self.assertEquals(n, len(plan.changes))
        self.assertEquals(n, provider.apply(plan))

        provider._client._request.assert_has_calls([
            # get all domains to build the cache
            call('GET', '/'),
        ])
        # These two checks are broken up so that ordering doesn't break things.
        # Python3 doesn't make the calls in a consistent order so different
        # things follow the GET / on different runs
        provider._client._request.assert_has_calls([
            call('POST', '/123123/records/SRV', data={
                'roundRobin': [{
                    'priority': 10,
                    'weight': 20,
                    'value': 'foo-1.unit.tests.',
                    'port': 30
                }, {
                    u'priority': 12,
                    u'weight': 20,
                    u'value': 'foo-2.unit.tests.',
                    u'port': 30
                }],
                u'name': u'_srv._tcp',
                u'ttl': 600
            }),
            call(u'POST', u'/domains/123123/records/SRV', data={
                u'roundRobin': [{
                    u'priority': 10,
                    u'weight': 20,
                    u'value': 'foo-1.unit.tests.',
                    u'port': 30
                }, {
                    u'priority': 12,
                    u'weight': 20,
                    u'value': 'foo-2.unit.tests.',
                    u'port': 30
                }],
                u'name': u'_srv._tcp',
                u'ttl': 600
            }),
            call(u'POST', u'/domains/123123/records/CNAME', data={
                u'host': 'unit.tests.',
                u'name': u'cname',
                u'ttl': 300
            }),
            call(u'POST', u'/domains/123123/records/NS', data={
                u'roundRobin': [{
                    u'value': u'ns1.unit.tests.'
                }, {
                    u'value': u'ns2.unit.tests.'
                }],
                u'name': u'under',
                u'ttl': 3600
            }),
            call(u'POST', u'/domains/123123/records/TXT', data={
                u'roundRobin': [{
                    u'value': u'"Bah bah black sheep"'
                }, {
                    u'value': u'"have you any wool."'
                }, {
                    u'value': u'"v=DKIM1;k=rsa;s=email;h=sha256;' + \
                              'p=A/kinda+of/long/string+with+numb3rs"'
                }],
                u'name': u'txt',
                u'ttl': 600
            }),
            call(u'POST', u'/domains/123123/records/ANAME', data={
                u'roundRobin': [{
                    u'disableFlag': False,
                    u'value': u'aname.unit.tests.'
                }],
                u'name': u'sub',
                u'ttl': 1800
            }),
            call(u'POST', u'/domains/123123/records/A', data={
                u'roundRobin': [{
                    u'value': '1.2.3.4'
                }, {
                    u'value': '1.2.3.5'
                }],
                u'name': '',
                u'ttl': 300
            }),
            call(u'POST', u'/domains/123123/records/A', data={
                u'roundRobin': [{
                    u'value': '2.2.3.6'
                }],
                u'name': u'www',
                u'ttl': 300
            }),
            call(u'POST', u'/domains/123123/records/ANAME', data={
                u'roundRobin': [{
                    u'disableFlag': False,
                    u'value': u'aname.unit.tests.'
                }],
                u'name': u'',
                u'ttl': 1800
            }),
            call(u'POST', u'/domains/123123/records/CNAME', data={
                u'host': 'unit.tests.',
                u'name': u'included',
                u'ttl': 3600
            }),
            call(u'POST', u'/domains/123123/records/AAAA', data={
                u'roundRobin': [{
                    u'value': '2601:644:500:e210:62f8:1dff:feb8:947a'
                }],
                u'name': u'aaaa',
                u'ttl': 600
            }),
            call(u'POST', u'/domains/123123/records/MX', data={
                u'roundRobin': [{
                    u'value': 'smtp-4.unit.tests.', u'level': 10
                }, {
                    u'value': 'smtp-2.unit.tests.', u'level': 20
                }, {
                    u'value': 'smtp-3.unit.tests.', u'level': 30
                }, {
                    u'value': 'smtp-1.unit.tests.', u'level': 40
                }],
                u'name': u'mx',
                u'value': 'smtp-1.unit.tests.',
                u'ttl': 300
            }),
            call(u'POST', u'/domains/123123/records/CAA', data={
                u'roundRobin': [{
                    u'flag': 0,
                    u'tag': 'issue',
                    u'data': 'ca.unit.tests'
                }],
                u'name': '',
                u'ttl': 3600
            }),
            call(u'POST', u'/domains/123123/records/PTR', data={
                u'host': 'foo.bar.com.',
                u'name': u'ptr',
                u'ttl': 300
            }),
            call(u'POST', u'/domains/123123/records/A', data={
                u'roundRobin': [{
                    u'value': '2.2.3.6'
                }],
                u'name': u'www.sub',
                u'ttl': 300
            }),
            call(u'POST', u'/domains/123123/records/NAPTR', data={
                u'roundRobin': [{
                    u'service': 'SIP+D2U',
                    u'regularExpression': '!^.*$!sip:info@bar.example.com!',
                    u'flags': 'S',
                    u'preference': 100,
                    u'order': 10,
                    u'replacement': '.'
                }, {
                    u'service': 'SIP+D2U',
                    u'regularExpression': '!^.*$!sip:info@bar.example.com!',
                    u'flags': 'U',
                    u'preference': 100,
                    u'order': 100,
                    u'replacement': '.'
                }],
                u'name': u'naptr',
                u'ttl': 600
            }),
            call(u'POST', u'/domains/123123/records/SPF', data={
                u'roundRobin': [{
                    u'value': u'"v=spf1 ip4:192.168.0.1/16-all"'
                }],
                u'name': u'spf',
                u'ttl': 600
            })
        ])
        self.assertEquals(20, provider._client._request.call_count)

        provider._client._request.reset_mock()

        # delete 1 and update 1
        provider._client.records = Mock(return_value=[
            {
                'id': 11189897,
                'name': 'www',
                'value': '1.2.3.4',
                'ttl': 300,
                'type': 'A',
            },
            {
                'id': 11189898,
                'name': 'www',
                'value': '2.2.3.4',
                'ttl': 300,
                'type': 'A',
            },
            {
                'id': 11189899,
                'name': 'ttl',
                'value': '3.2.3.4',
                'ttl': 600,
                'value': [
                    '3.2.3.4'
                ]
            },  {
                'id': 11189899,
                'type': 'ALIAS',
                'name': 'alias',
                'ttl': 600,
                'value': [{
                    'value': 'aname.unit.tests.'
                }]
            }
        ])

        # Domain exists, we don't care about return
        # resp.json.side_effect = ['{}']
        provider._client.domains = Mock(return_value={
            "unit.tests.": 123123
        })

        resp = Mock()
        resp.json = Mock(return_value={
            "id": 123123,
            "name": "unit.tests.",
            "soa": {
                "primaryNameserver": "ns11.constellix.com.",
                "email": "dns.constellix.com.",
                "ttl": 86400,
                "serial": 2015010118,
                "refresh": 43200,
                "retry": 3600,
                "expire": 1209600,
                "negCache": 180
            },
            "createdTs": "2019-08-02T12:52:10Z",
            "modifiedTs": "2019-08-13T11:45:59Z",
            "typeId": 1,
            "domainTags": [],
            "hasGtdRegions": False,
            "hasGeoIP": False,
            "nameserverGroup": 1,
            "nameservers": [
                "ns11.constellix.com.",
                "ns21.constellix.com.",
                "ns31.constellix.com.",
                "ns41.constellix.net.",
                "ns51.constellix.net.",
                "ns61.constellix.net."
            ],
            "note": "",
            "status": "ACTIVE",
        })
        provider._client._request = Mock(return_value=resp)

        wanted = Zone('unit.tests.', [])
        wanted.add_record(Record.new(wanted, 'ttl', {
            'ttl': 300,
            'type': 'A',
            'value': '3.2.3.4'
        }))

        plan = provider.plan(wanted)
        self.assertEquals(3, len(plan.changes))
        self.assertEquals(3, provider.apply(plan))

        # recreate for update, and deletes for the 2 parts of the other
        provider._client._request.assert_has_calls([
            call(u'GET', u'/domains/123123'),
            call(u'DELETE', u'/domains/123123/records/A/11189899'),
            call(u'POST', u'/domains/123123/records/A', data={
                u'roundRobin': [{
                    u'value': u'3.2.3.4'
                }],
                u'name': u'ttl',
                u'ttl': 300
            }),
            call('DELETE', '/123123/records/A/11189897'),
            call('DELETE', '/123123/records/A/11189898'),
            call('DELETE', '/123123/records/ANAME/11189899')
        ], any_order=True)
