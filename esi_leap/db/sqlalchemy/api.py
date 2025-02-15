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

import sys
import threading

from oslo_config import cfg
from oslo_db.sqlalchemy import enginefacade
from oslo_log import log as logging

import sqlalchemy as sa
from sqlalchemy import or_

from esi_leap.common import exception
from esi_leap.common import keystone
from esi_leap.common import statuses
from esi_leap.db.sqlalchemy import models


CONF = cfg.CONF
LOG = logging.getLogger(__name__)

_CONTEXT = threading.local()


def get_backend():
    """The backend is this module itself."""
    return sys.modules[__name__]


def _session_for_read():
    return enginefacade.reader.using(_CONTEXT)


def _session_for_write():
    return enginefacade.writer.using(_CONTEXT)


def model_query(model, *args):
    """Query helper.

    :param model: base model to query
    """
    with _session_for_read() as session:
        query = session.query(model, *args)
        return query


# Helpers for building constraints / equality checks


def constraint(**conditions):
    return Constraint(conditions)


def equal_any(*values):
    return EqualityCondition(values)


def not_equal(*values):
    return InequalityCondition(values)


class Constraint(object):
    def __init__(self, conditions):
        self.conditions = conditions

    def apply(self, model, query):
        for key, condition in self.conditions.items():
            for clause in condition.clauses(getattr(model, key)):
                query = query.filter(clause)
        return query


class EqualityCondition(object):
    def __init__(self, values):
        self.values = values

    def clauses(self, field):
        return sa.or_([field == value for value in self.values])


class InequalityCondition(object):
    def __init__(self, values):
        self.values = values

    def clauses(self, field):
        return [field != value for value in self.values]


# Offer
def offer_get_by_uuid(offer_uuid):
    query = model_query(models.Offer)
    offer_ref = query.filter_by(uuid=offer_uuid).one_or_none()
    return offer_ref


def offer_get_by_name(name):
    query = model_query(models.Offer)
    offers = query.filter_by(name=name).all()
    return offers


def offer_get_all(filters):

    query = model_query(models.Offer)

    lessee_id = filters.pop('lessee_id', None)
    start = filters.pop('start_time', None)
    end = filters.pop('end_time', None)
    time_filter_type = filters.pop('time_filter_type', None)
    a_start = filters.pop('available_start_time', None)
    a_end = filters.pop('available_end_time', None)

    query = query.filter_by(**filters)

    if lessee_id:
        lessee_id_list = keystone.get_parent_project_id_tree(lessee_id)
        query = query.filter(or_(models.Offer.project_id == lessee_id,
                                 models.Offer.lessee_id.__eq__(None),
                                 models.Offer.lessee_id.in_(lessee_id_list)))

    if start and end:
        if time_filter_type == 'within':
            query = query.filter(((start <= models.Offer.start_time) &
                                  (end >= models.Offer.start_time)) |

                                 ((start <= models.Offer.end_time) &
                                  (end >= models.Offer.end_time)))
        else:
            query = query.filter((start >= models.Offer.start_time) &
                                 (end <= models.Offer.end_time))

    if a_start and a_end:
        for o in query:
            try:
                offer_verify_availability(o, a_start, a_end)
            except exception.OfferNoTimeAvailabilities:
                query = query.filter(models.Offer.uuid != o.uuid)

    return query


def offer_get_conflict_times(offer_ref):

    l_query = model_query(models.Lease)

    return l_query.with_entities(
        models.Lease.start_time, models.Lease.end_time).\
        join(models.Offer).\
        order_by(models.Lease.start_time).\
        filter(models.Lease.offer_uuid == offer_ref.uuid,
               (models.Lease.status == statuses.CREATED) |
               (models.Lease.status == statuses.ACTIVE)
               ).all()


def offer_get_first_availability(offer_uuid, start):
    l_query = model_query(models.Lease)

    return l_query.with_entities(
        models.Lease.start_time).\
        filter(models.Lease.offer_uuid == offer_uuid,
               (models.Lease.status == statuses.CREATED) |
               (models.Lease.status == statuses.ACTIVE)
               ).\
        order_by(models.Lease.start_time).\
        filter(models.Lease.end_time >= start).first()


def offer_verify_availability(offer_ref, start, end):

    if start < offer_ref.start_time or end > offer_ref.end_time:
        raise exception.OfferNoTimeAvailabilities(offer_uuid=offer_ref.uuid,
                                                  start_time=start,
                                                  end_time=end)

    l_query = model_query(models.Lease)

    leases = l_query.with_entities(
        models.Lease.start_time, models.Lease.end_time).\
        filter((models.Lease.offer_uuid == offer_ref.uuid),
               (models.Lease.status == statuses.CREATED) |
               (models.Lease.status == statuses.ACTIVE)
               )

    conflict = leases.filter((
        ((start >= models.Lease.start_time) &
         (start < models.Lease.end_time)) |

        ((end > models.Lease.start_time) &
         (end <= models.Lease.end_time)) |

        ((start <= models.Lease.start_time) &
         (end >= models.Lease.end_time))
    )).first()

    if conflict:
        raise exception.OfferNoTimeAvailabilities(offer_uuid=offer_ref.uuid,
                                                  start_time=start,
                                                  end_time=end)


def offer_create(values):
    offer_ref = models.Offer()
    offer_ref.update(values)

    with _session_for_write() as session:
        session.add(offer_ref)
        session.flush()
        return offer_ref


def offer_update(offer_uuid, values):

    with _session_for_write() as session:

        query = model_query(models.Offer)
        offer_ref = query.filter_by(uuid=offer_uuid).one_or_none()

        values.pop('uuid', None)
        values.pop('project_id', None)

        start = values.get('start_time', None)
        end = values.get('end_time', None)
        if start is None:
            start = offer_ref.start_time
        if end is None:
            end = offer_ref.end_time
        if start >= end:
            raise exception.InvalidTimeRange(resource="an offer",
                                             start_time=str(start),
                                             end_time=str(end))

        offer_ref.update(values)
        session.flush()
        return offer_ref


def offer_destroy(offer_uuid):
    with _session_for_write() as session:
        query = model_query(models.Offer)
        offer_ref = query.filter_by(uuid=offer_uuid).one_or_none()

        if not offer_ref:
            raise exception.OfferNotFound(offer_uuid=offer_uuid)

        model_query(models.Offer).filter_by(uuid=offer_uuid).delete()
        session.flush()


# Leases
def lease_get_by_uuid(lease_uuid):
    query = model_query(models.Lease)
    result = query.filter_by(uuid=lease_uuid).one_or_none()
    return result


def lease_get_by_name(name):
    query = model_query(models.Lease)
    leases = query.filter_by(name=name).all()
    return leases


def lease_get_all(filters):
    query = model_query(models.Lease)

    start = filters.pop('start_time', None)
    end = filters.pop('end_time', None)
    time_filter_type = filters.pop('time_filter_type', None)
    status = filters.pop('status', None)
    project_or_owner_id = filters.pop('project_or_owner_id', None)

    query = query.filter_by(**filters)

    if status:
        query = query.filter((models.Lease.status.in_(status)))

    if start and end:
        if time_filter_type == 'within':
            query = query.filter(((start <= models.Lease.start_time) &
                                  (end >= models.Lease.start_time)) |

                                 ((start <= models.Lease.end_time) &
                                  (end >= models.Lease.end_time)))
        else:
            query = query.filter((start >= models.Lease.start_time) &
                                 (end <= models.Lease.end_time))

    if project_or_owner_id:
        query = query.filter(
            (project_or_owner_id == models.Lease.project_id) |
            (project_or_owner_id == models.Lease.owner_id))

    return query


def lease_create(values):
    lease_ref = models.Lease()
    lease_ref.update(values)

    with _session_for_write() as session:
        session.add(lease_ref)
        session.flush()
        return lease_ref


def lease_update(lease_uuid, values):
    with _session_for_write() as session:
        query = model_query(models.Lease)
        lease_ref = query.filter_by(uuid=lease_uuid).one_or_none()

        values.pop('uuid', None)
        values.pop('project_id', None)

        start = values.get('start_time', None)
        end = values.get('end_time', None)
        if start is None:
            start = lease_ref.start_time
        if end is None:
            end = lease_ref.end_time
        if start >= end:
            raise exception.InvalidTimeRange(resource="a lease",
                                             start_time=str(start),
                                             end_time=str(end))

        lease_ref.update(values)
        session.flush()
        return lease_ref


def lease_destroy(lease_uuid):
    with _session_for_write() as session:

        query = model_query(models.Lease)
        lease_ref = query.filter_by(uuid=lease_uuid).one_or_none()

        if not lease_ref:
            raise exception.LeaseNotFound(lease_uuid=lease_uuid)
        query.delete()
        session.flush()


# Owner Changes
def owner_change_get_by_uuid(owner_change_uuid):
    query = model_query(models.OwnerChange)
    result = query.filter_by(uuid=owner_change_uuid).one_or_none()
    return result


def owner_change_get_all(filters):
    query = model_query(models.OwnerChange)

    start = filters.pop('start_time', None)
    end = filters.pop('end_time', None)
    status = filters.pop('status', None)
    from_or_to_owner_id = filters.pop('from_or_to_owner_id', None)

    query = query.filter_by(**filters)

    if status:
        query = query.filter((models.OwnerChange.status.in_(status)))

    if start and end:
        query = query.filter((start >= models.OwnerChange.start_time) &
                             (end <= models.OwnerChange.end_time))

    if from_or_to_owner_id:
        query = query.filter(
            (from_or_to_owner_id == models.OwnerChange.from_owner_id) |
            (from_or_to_owner_id == models.OwnerChange.to_owner_id))

    return query


def owner_change_create(values):
    owner_change_ref = models.OwnerChange()
    owner_change_ref.update(values)

    with _session_for_write() as session:
        session.add(owner_change_ref)
        session.flush()
        return owner_change_ref


def owner_change_update(owner_change_uuid, values):
    with _session_for_write() as session:
        query = model_query(models.OwnerChange)
        owner_change_ref = query.filter_by(
            uuid=owner_change_uuid).one_or_none()

        values.pop('uuid', None)
        values.pop('from_owner_id', None)
        values.pop('to_owner_id', None)
        values.pop('resource_type', None)
        values.pop('resource_uuid', None)

        start = values.get('start_time', None)
        end = values.get('end_time', None)
        if start is None:
            start = owner_change_ref.start_time
        if end is None:
            end = owner_change_ref.end_time
        if start >= end:
            raise exception.InvalidTimeRange(resource="an owner_change",
                                             start_time=str(start),
                                             end_time=str(end))

        owner_change_ref.update(values)
        session.flush()
        return owner_change_ref


def owner_change_destroy(owner_change_uuid):
    with _session_for_write() as session:

        query = model_query(models.OwnerChange)
        owner_change_ref = query.filter_by(
            uuid=owner_change_uuid).one_or_none()

        if not owner_change_ref:
            raise exception.OwnerChangeNotFound(
                owner_change_uuid=owner_change_uuid)
        query.delete()
        session.flush()


# Resources
def resource_verify_availability(r_type, r_uuid, start, end,
                                 is_owner_change=False):
    # check conflict with offers
    o_query = model_query(models.Offer)

    offers = o_query.with_entities(
        models.Offer.start_time, models.Offer.end_time).\
        filter((models.Offer.resource_uuid == r_uuid),
               (models.Offer.resource_type == r_type),
               (models.Offer.status == statuses.AVAILABLE))

    offer_conflict = offers.filter((
        ((start >= models.Offer.start_time) &
         (start < models.Offer.end_time)) |

        ((end > models.Offer.start_time) &
         (end <= models.Offer.end_time)) |

        ((start <= models.Offer.start_time) &
         (end >= models.Offer.end_time))
    )).first()

    if offer_conflict:
        raise exception.ResourceTimeConflict(
            resource_uuid=r_uuid,
            resource_type=r_type)

    # check conflict with leases
    l_query = model_query(models.Lease)

    leases = l_query.with_entities(
        models.Lease.start_time, models.Lease.end_time).\
        filter((models.Lease.resource_uuid == r_uuid),
               (models.Lease.resource_type == r_type),
               (models.Lease.status.in_([statuses.CREATED, statuses.ACTIVE])))

    lease_conflict = leases.filter((
        ((start >= models.Lease.start_time) &
         (start < models.Lease.end_time)) |

        ((end > models.Lease.start_time) &
         (end <= models.Lease.end_time)) |

        ((start <= models.Lease.start_time) &
         (end >= models.Lease.end_time))
    )).first()

    if lease_conflict:
        raise exception.ResourceTimeConflict(
            resource_uuid=r_uuid,
            resource_type=r_type)

    # check conflict with ownership changes
    if not is_owner_change:
        # check_resource_admin will have been called earlier
        # for leases and offers
        return

    oc_query = model_query(models.OwnerChange)

    ocs = oc_query.with_entities(
        models.OwnerChange.start_time, models.OwnerChange.end_time).\
        filter((models.OwnerChange.resource_uuid == r_uuid),
               (models.OwnerChange.resource_type == r_type),
               (models.OwnerChange.status.in_([statuses.CREATED,
                                               statuses.ACTIVE])))

    oc_conflict = ocs.filter((
        ((start >= models.OwnerChange.start_time) &
         (start < models.OwnerChange.end_time)) |

        ((end > models.OwnerChange.start_time) &
         (end <= models.OwnerChange.end_time)) |

        ((start <= models.OwnerChange.start_time) &
         (end >= models.OwnerChange.end_time))
    )).first()

    if oc_conflict:
        raise exception.ResourceTimeConflict(
            resource_uuid=r_uuid,
            resource_type=r_type)


def resource_check_admin(resource_type, resource_uuid,
                         start_time, end_time,
                         default_admin_project_id, project_id):
    # check if time period straddles an owner changes
    ocs_conflicts = model_query(models.OwnerChange).with_entities(
        models.OwnerChange.start_time, models.OwnerChange.end_time).\
        filter((models.OwnerChange.resource_uuid == resource_uuid),
               (models.OwnerChange.resource_type == resource_type),
               (models.OwnerChange.status.in_([statuses.CREATED,
                                               statuses.ACTIVE])))

    ocs_conflicts = ocs_conflicts.filter((
        ((start_time >= models.OwnerChange.start_time) &
         (start_time < models.OwnerChange.end_time) &
         (end_time > models.OwnerChange.end_time)) |

        ((start_time < models.OwnerChange.start_time) &
         (end_time > models.OwnerChange.start_time) &
         (end_time < models.OwnerChange.end_time)) |

        ((start_time <= models.OwnerChange.start_time) &
         (end_time >= models.OwnerChange.end_time))
    ))

    if ocs_conflicts.count() > 0:
        return False

    # check if time period encompasses a single owner change
    filters = {
        'resource_type': resource_type,
        'resource_uuid': resource_uuid,
        'start_time': start_time,
        'end_time': end_time,
        'status': [statuses.CREATED, statuses.ACTIVE]
    }
    ocs = owner_change_get_all(filters)

    if ocs.count() > 1:
        # shouldn't happen, but...
        return False
    if ocs.count() == 1:
        return project_id == ocs[0].to_owner_id
    # no owner changes; use default check
    return project_id == default_admin_project_id
