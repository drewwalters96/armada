# Copyright 2017 The Armada Authors.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

from armada.conf import default

# Required Oslo configuration setup
default.register_opts()

from armada import Apply
from tiller import Release, Status

import falcon

from oslo_config import cfg
from oslo_log import log as logging

from keystonemiddleware import auth_token

LOG = logging.getLogger(__name__)
CONF = cfg.CONF
DOMAIN = "armada"

logging.register_options(CONF)
logging.setup(CONF, DOMAIN)

# Build API
app = falcon.API()

# Configure API routing
url_routes = (
    ('/tiller/status', Status()),
    ('/tiller/releases', Release()),
    ('/armada/apply/', Apply())
)

for route, service in url_routes:
    app.add_route(route, service)

# Wrap in auth middleware
api = auth_token.AuthProtocol(app, {})
