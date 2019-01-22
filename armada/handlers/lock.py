# Copyright 2019, AT&T Intellectual Property
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
import functools
import time
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor
from kubernetes import client
from kubernetes.client.rest import ApiException
from oslo_config import cfg
from oslo_log import log as logging

from armada.handlers.k8s import K8s

CONF = cfg.CONF

TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
# Lock configuration
LOCK_GROUP = "armada.process"
LOCK_VERSION = "v1"
LOCK_NAMESPACE = "kube-system"
LOCK_PLURAL = "locks"
LOCK_SINGULAR = "lock"

LOG = logging.getLogger(__name__)


class LockException(Exception):
    pass


def lock_and_thread(lock_name="lock"):
    """This function creates a thread to execute the wrapped function after
    acquiring a lock. While the thread is still running, this function
    periodically updates the lock

    :param lock_name: name of the lock to create
    """

    def lock_decorator(func):

        @functools.wraps(func)
        def func_wrapper(*args, **kwargs):
            with Lock(lock_name) as lock:
                pool = ThreadPoolExecutor(1)
                future = pool.submit(func, *args, **kwargs)
                start = time.time()
                while not future.done():
                    if time.time() - start > CONF.lock_update_interval:
                        lock.update_lock()
                        start = time.time()
                    time.sleep(1)
                return future.result()

        return func_wrapper

    return lock_decorator


class Lock:

    def __init__(self, lock_name, additional_data=None):
        """Creates a lock with the specified name and data. When a lock with
        that name already exists then this will continuously attempt to acquire
        it until:
            * the attempt times out
            * the lock is gone this is able to acquire a new lock
            * the existing lock expires, in which case this will forcibly
              remove it and continue attempting to acquire the lock

        :param lock_name: name of the lock resource to be created, locks with
            different names can coexist and won't conflict with each other
        :param additional_data: dict of any additional data to be added to the
            lock's `data` section
        """
        self.expire_time = CONF.lock_expiration
        self.timeout = CONF.lock_acquire_timeout
        self.acquire_delay = CONF.lock_acquire_delay
        self.lock_config = LockConfig(
            name=lock_name, additional_data=additional_data)

    def _test_lock_ownership(self):
        # If the uid of the current lock is the same as the one given when we
        # created the lock, then it must be the one created by this program
        lock = self.lock_config.get_lock()
        if lock:
            lock_uid = lock['metadata']['uid']
            current_uid = self.lock_config.metadata.get('uid', None)
            return current_uid == lock_uid
        # The lock must not exist
        return False

    def lock_age(self):
        lock = self.lock_config.get_lock()
        if lock:
            creation = lock['data']['lastUpdated']
            creation_time = datetime.strptime(creation, TIME_FORMAT)
            return datetime.utcnow() - creation_time
        # If no lock exists then 0 is returned so the lock is assuredly not old
        # enough to be expired
        return 0

    def acquire_lock(self):
        start = time.time()
        LOG.info("Acquiring lock")
        while (time.time() - start) < self.timeout:
            try:
                self.lock_config.create_lock()
                return True
            except ApiException as err:
                if err.status == 404:
                    LOG.info("Lock Custom Resource Definition not found, "
                             "creating now")
                    self.lock_config.create_definition()
                    continue
                elif err.status == 409:
                    # If the exception is 409 then there is already a lock, so
                    # we should continue with the rest of the logic
                    LOG.warn("There is already an existing lock")
                else:
                    raise
            if self._test_lock_ownership():
                # If there is already a lock that was created by this thread
                # then we must have successfully acquired the lock
                return True
            else:
                # There is a lock but it was not created by this thread, which
                # means that the only way it should be removed is if the age
                # of the lock exceeds the expire time in order to avoid
                # removing another thread's lock while it is still working
                if self.lock_age() > timedelta(seconds=self.expire_time):
                    LOG.info("Lock has exceeded expiry time, removing so"
                             "processing can continue")
                    self.release_lock()
                    continue
            LOG.debug("Sleeping before attempting to acquire lock again")
            time.sleep(self.acquire_delay)
        raise LockException("Unable to acquire lock before timeout")

    def release_lock(self):
        LOG.info("Releasing lock")
        return self.lock_config.delete_lock()

    def update_lock(self):
        LOG.debug("Updating lock")
        self.lock_config.replace_lock()

    def __enter__(self):
        self.acquire_lock()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release_lock()
        return False


class LockConfig:

    def __init__(self, name, additional_data=None):
        self.name = name
        data = additional_data or dict()
        self.full_name = "{}.{}.{}".format(LOCK_PLURAL, LOCK_GROUP, self.name)
        self.metadata = {'name': self.full_name}
        self.body = {
            'kind': "Resource",
            'apiVersion': "{}/{}".format(LOCK_GROUP, LOCK_VERSION),
            'metadata': self.metadata,
            'data': data
        }
        self.delete_options = {}

        self.k8s = K8s()

    def create_lock(self):
        """ Creates the Lock custom resource object
        :return: the Lock custom resource object
        :rtype: object
        """
        self.body['data']['lastUpdated'] = \
            datetime.utcnow().strftime(TIME_FORMAT)
        lock = self.k8s.create_custom_resource(
            group=LOCK_GROUP,
            version=LOCK_VERSION,
            namespace=LOCK_NAMESPACE,
            plural=LOCK_PLURAL,
            body=self.body)

        self.metadata = lock.get('metadata', self.metadata)
        return lock

    def get_lock(self):
        """Retrieves the Lock custom resource object

        :return: the Lock custom resource object
        :rtype: object
        """
        try:
            return self.k8s.read_custom_resource(
                group=LOCK_GROUP,
                version=LOCK_VERSION,
                namespace=LOCK_NAMESPACE,
                plural=LOCK_PLURAL,
                name=self.full_name)

        except ApiException as err:
            if err.status == 404:
                return None
            raise

    def delete_lock(self):
        """Deletes the Lock custom resource

        :return: whether it was successfully deleted
        :rtype: bool
        """
        try:
            self.k8s.delete_custom_resource(
                group=LOCK_GROUP,
                version=LOCK_VERSION,
                namespace=LOCK_NAMESPACE,
                plural=LOCK_PLURAL,
                name=self.full_name,
                body=self.delete_options)

            return True
        except ApiException as err:
            # If it returns 404 then something else deleted it
            if err.status == 404:
                return True
            raise

    def replace_lock(self):
        """Updates the Lock custom resource object with a new lastUpdated time

        :return: the Lock custom resource object
        :rtype: object
        """
        self.body['metadata']['resourceVersion'] = \
            self.metadata['resourceVersion']
        self.body['data']['lastUpdated'] = \
            datetime.utcnow().strftime(TIME_FORMAT)
        lock = self.k8s.replace_custom_resource(
            group=LOCK_GROUP,
            version=LOCK_VERSION,
            namespace=LOCK_NAMESPACE,
            plural=LOCK_PLURAL,
            name=self.full_name,
            body=self.body)

        self.metadata = lock.get('metadata', self.metadata)
        return lock

    def create_definition(self):
        names = client.V1beta1CustomResourceDefinitionNames(
            kind="Resource", plural=LOCK_PLURAL, singular=LOCK_SINGULAR)
        metadata = client.V1ObjectMeta(
            name="{}.{}".format(LOCK_PLURAL, LOCK_GROUP),
            resource_version=LOCK_VERSION)
        status = client.V1beta1CustomResourceDefinitionStatus(
            accepted_names=names,
            conditions=[],
            stored_versions=[LOCK_VERSION])
        spec = client.V1beta1CustomResourceDefinitionSpec(
            group=LOCK_GROUP,
            names=names,
            scope="Namespaced",
            version=LOCK_VERSION)
        crd = client.V1beta1CustomResourceDefinition(
            spec=spec,
            status=status,
            metadata=metadata,
            kind="CustomResourceDefinition")
        try:
            self.k8s.create_custom_resource_definition(crd)
        except ValueError as err:
            # Because of an issue with the Kubernetes code, the API server
            # may return `null` for the required field `conditions` in
            # kubernetes.client.V1beta1CustomResourceDefinitionStatus
            # This causes validation to fail which will raise the subsequent
            # ValueError even though the CRD was created successfully
            # https://github.com/kubernetes-client/gen/issues/52
            # TODO if this is fixed upstream this should be removed
            known_msg = "Invalid value for `conditions`, must not be `None`"
            known_err = ValueError(known_msg)
            if err.args != known_err.args:
                raise
            LOG.debug("Encountered known issue while creating CRD, continuing")
        except ApiException as err:
            # If a 409 is received then the definition already exists
            if err.status != 409:
                raise
