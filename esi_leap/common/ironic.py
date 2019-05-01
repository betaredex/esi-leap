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

from keystoneauth1 import loading as ks_loading

from ironicclient import client as ironic_client

import esi_leap.conf


CONF = esi_leap.conf.CONF
_cached_ironic_client = None


def get_ironic_client():
    global _cached_ironic_client
    if _cached_ironic_client is not None:
        return _cached_ironic_client

    auth_plugin = ks_loading.load_auth_from_conf_options(CONF, 'ironic')
    sess = ks_loading.load_session_from_conf_options(CONF, 'ironic',
                                                     auth=auth_plugin)

    kwargs = {}
    cli = ironic_client.get_client(1,
                                   session=sess, **kwargs)
    _cached_ironic_client = cli

    return cli


def get_node_project_owner_id(node_uuid):
    node = get_ironic_client().node.get(node_uuid)
    return node.properties.get('project_owner_id', None)


def get_node_project_id(node_uuid):
    node = get_ironic_client().node.get(node_uuid)
    return node.properties.get('project_id', None)


def set_node_project_id(node_uuid, project_id):
    if project_id is None:
        if not get_node_project_id(node_uuid):
            return
        patch = {
            "op": "remove",
            "path": "/properties/project_id",
        }
    else:
        patch = {
            "op": "add",
            "path": "/properties/project_id",
            "value": project_id,
        }
    get_ironic_client().node.update(node_uuid, [patch])
