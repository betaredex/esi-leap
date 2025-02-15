#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

import datetime
import mock
from oslo_utils import uuidutils
import tempfile
import threading

from esi_leap.common import exception
from esi_leap.common import statuses
from esi_leap.objects import offer
from esi_leap.tests import base


class TestOfferObject(base.DBTestCase):

    def setUp(self):
        super(TestOfferObject, self).setUp()

        start = datetime.datetime(2016, 7, 16, 19, 20, 30)
        self.test_offer_data = {
            'id': 27,
            'name': "o",
            'uuid': uuidutils.generate_uuid(),
            'project_id': '0wn5r',
            'lessee_id': None,
            'resource_type': 'dummy_node',
            'resource_uuid': '1718',
            'start_time': start,
            'end_time': start + datetime.timedelta(days=100),
            'status': statuses.AVAILABLE,
            'properties': {'floor_price': 3},
            'created_at': None,
            'updated_at': None
        }
        self.config(lock_path=tempfile.mkdtemp(), group='oslo_concurrency')

    @mock.patch('esi_leap.db.sqlalchemy.api.offer_get_by_uuid')
    def test_get(self, mock_offer_get_by_uuid):
        offer_uuid = self.test_offer_data['uuid']
        mock_offer_get_by_uuid.return_value = self.test_offer_data

        o = offer.Offer.get(offer_uuid, self.context)

        mock_offer_get_by_uuid.assert_called_once_with(offer_uuid)
        self.assertEqual(self.context, o._context)

    @mock.patch('esi_leap.db.sqlalchemy.api.offer_get_all')
    def test_get_all(self, mock_offer_get_all):
        mock_offer_get_all.return_value = [
            self.test_offer_data]

        offers = offer.Offer.get_all({}, self.context)

        mock_offer_get_all.assert_called_once_with({})
        self.assertEqual(len(offers), 1)
        self.assertIsInstance(offers[0], offer.Offer)
        self.assertEqual(self.context, offers[0]._context)

    @mock.patch('esi_leap.db.sqlalchemy.api.offer_get_conflict_times')
    def test_get_availabilities(self, mock_offer_get_conflict_times):
        o = offer.Offer(
            self.context, **self.test_offer_data)
        mock_offer_get_conflict_times.return_value = [
            [
                o.start_time + datetime.timedelta(days=10),
                o.start_time + datetime.timedelta(days=20)
            ],
            [
                o.start_time + datetime.timedelta(days=20),
                o.start_time + datetime.timedelta(days=30)
            ],
            [
                o.start_time + datetime.timedelta(days=50),
                o.start_time + datetime.timedelta(days=60)
            ]
        ]

        expect = [
            [
                o.start_time,
                o.start_time + datetime.timedelta(days=10)
            ],
            [
                o.start_time + datetime.timedelta(days=30),
                o.start_time + datetime.timedelta(days=50)
            ],
            [
                o.start_time + datetime.timedelta(days=60),
                o.end_time
            ],
        ]
        a = o.get_availabilities()
        self.assertEqual(a, expect)

        mock_offer_get_conflict_times.return_value = [
            [
                o.start_time,
                o.end_time
            ],
        ]

        expect = []
        a = o.get_availabilities()
        self.assertEqual(a, expect)

        mock_offer_get_conflict_times.return_value = []

        expect = [
            [
                o.start_time,
                o.end_time
            ],
        ]
        a = o.get_availabilities()
        self.assertEqual(a, expect)

    @mock.patch('esi_leap.db.sqlalchemy.api.resource_verify_availability')
    @mock.patch('esi_leap.db.sqlalchemy.api.offer_create')
    def test_create(self, mock_oc, mock_rva):
        o = offer.Offer(
            self.context, **self.test_offer_data)
        mock_oc.return_value = self.test_offer_data

        o.create(self.context)

        mock_rva.assert_called_once_with(o.resource_type,
                                         o.resource_uuid,
                                         o.start_time,
                                         o.end_time,
                                         is_owner_change=False)
        mock_oc.assert_called_once_with(self.test_offer_data)

    def test_create_invalid_time(self):
        start = self.test_offer_data['start_time']
        bad_offer = {
            'id': 27,
            'name': "o",
            'uuid': '534653c9-880d-4c2d-6d6d-11111111111',
            'project_id': '0wn5r',
            'resource_type': 'dummy_node',
            'resource_uuid': '1718',
            'start_time': start + datetime.timedelta(days=100),
            'end_time': start,
            'status': statuses.AVAILABLE,
            'properties': {'floor_price': 3},
            'created_at': None,
            'updated_at': None
        }

        o = offer.Offer(
            self.context, **bad_offer)

        self.assertRaises(exception.InvalidTimeRange, o.create)

    @mock.patch('esi_leap.db.sqlalchemy.api.resource_verify_availability')
    @mock.patch('esi_leap.db.sqlalchemy.api.offer_create')
    def test_create_concurrent(self, mock_oc, mock_rva):
        o = offer.Offer(
            self.context, **self.test_offer_data)
        o2 = offer.Offer(
            self.context, **self.test_offer_data)

        o2.id = 28

        def update_mock(updates):
            mock_rva.side_effect = Exception("bad")

        mock_oc.side_effect = update_mock

        thread = threading.Thread(target=o.create)
        thread2 = threading.Thread(target=o2.create)

        thread.start()
        thread2.start()

        thread.join()
        thread2.join()

        assert mock_rva.call_count == 2
        mock_oc.assert_called_once()

    @mock.patch('esi_leap.db.sqlalchemy.api.offer_destroy')
    def test_destroy(self, mock_offer_destroy):
        o = offer.Offer(self.context, **self.test_offer_data)
        o.destroy()
        mock_offer_destroy.assert_called_once_with(o.uuid)

    @mock.patch('esi_leap.db.sqlalchemy.api.offer_update')
    def test_save(self, mock_offer_update):
        o = offer.Offer(self.context, **self.test_offer_data)
        new_status = statuses.CANCELLED
        updated_at = datetime.datetime(2006, 12, 11, 0, 0)

        updated_offer = self.test_offer_data.copy()
        updated_offer['status'] = new_status
        updated_offer['updated_at'] = updated_at
        mock_offer_update.return_value = updated_offer

        o.status = new_status
        o.save(self.context)

        updated_values = self.test_offer_data.copy()
        updated_values['status'] = new_status
        mock_offer_update.assert_called_once_with(
            o.uuid, updated_values)
        self.assertEqual(self.context, o._context)
        self.assertEqual(updated_at, o.updated_at)

    @mock.patch('esi_leap.db.sqlalchemy.api.offer_verify_availability')
    def test_verify_availability(self, mock_ova):
        o = offer.Offer(self.context, **self.test_offer_data)
        o.verify_availability(o.start_time, o.end_time)
        mock_ova.assert_called_once_with(o, o.start_time, o.end_time)

    @mock.patch('esi_leap.resource_objects.resource_object_factory.'
                'ResourceObjectFactory.get_resource_object')
    def test_resource_object(self, mock_gro):
        o = offer.Offer(self.context, **self.test_offer_data)
        o.resource_object()
        mock_gro.assert_called_once_with(o.resource_type, o.resource_uuid)
