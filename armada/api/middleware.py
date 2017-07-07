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

from keystonemiddleware import auth_token
import falcon

class AuthMiddleware(object):
    def process_request(self, req, resp):
        auth_token.AuthProtocol(app, conf)



        # token = req.get_header('X-Auth-Token')
        # self.validate_token(token)

    def validate_token(self, token):
        if token is None:
            raise falcon.HTTPUnauthorized('X-Auth token invalid.')
        if token != 'armada':
            raise falcon.HTTPUnauthorized('X-Auth token invalid.')
