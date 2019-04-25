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
from copy import deepcopy

from oslo_log import log as logging

from armada import const
from armada import exceptions
from armada.handlers import schema

LOG = logging.getLogger(__name__)


class Manifest(object):

    def __init__(self, documents, target_manifest=None):
        """Instantiates a Manifest object.

        An Armada Manifest expects that at least one of each of the following
        be included in ``documents``:

        * A document with schema "armada/Chart/v1"
        * A document with schema "armada/ChartGroup/v1"

        And only one document of the following is allowed:

        * A document with schema "armada/Manifest/v1"

        If multiple documents with schema "armada/Manifest/v1" are provided,
        specify ``target_manifest`` to select the target one.

        :param List[dict] documents: Documents out of which to build the
            Armada Manifest.
        :param str target_manifest: The target manifest to use when multiple
            documents with "armada/Manifest/v1" are contained in
            ``documents``. Default is None.
        :raises ManifestException: If the expected number of document types
            are not found or if the document types are missing required
            properties.
        """
        self.documents = deepcopy(documents)
        self.charts, self.groups, manifests = self._find_documents(
            target_manifest)

        if len(manifests) > 1:
            error = ('Multiple manifests are not supported. Ensure that the '
                     '`target_manifest` option is set to specify the target '
                     'manifest')
            LOG.error(error)
            raise exceptions.ManifestException(details=error)
        else:
            self.manifest = manifests[0] if manifests else None

        if not all([self.charts, self.groups, self.manifest]):
            expected_schemas = [schema.TYPE_CHART, schema.TYPE_CHARTGROUP]
            error = ('Documents must include at least one of each of {} '
                     'and only one {}').format(expected_schemas,
                                               schema.TYPE_MANIFEST)
            LOG.error(error)
            raise exceptions.ManifestException(details=error)

    def _find_documents(self, target_manifest=None):
        """Returns the chart documents, chart group documents,
        and Armada manifest

        If multiple documents with schema "armada/Manifest/v1" are provided,
        specify ``target_manifest`` to select the target one.

        :param str target_manifest: The target manifest to use when multiple
            documents with "armada/Manifest/v1" are contained in
            ``documents``. Default is None.
        :returns: Tuple of chart documents, chart groups, and manifests
            found in ``self.documents``
        :rtype: tuple
        """
        charts = []
        groups = []
        manifests = []
        for document in self.documents:
            schema_info = schema.get_schema_info(document.get('schema'))
            if not schema_info:
                continue
            if schema_info.type == schema.TYPE_CHART:
                charts.append(document)
            if schema_info.type == schema.TYPE_CHARTGROUP:
                groups.append(document)
            if schema_info.type == schema.TYPE_MANIFEST:
                manifest_name = document.get('metadata', {}).get('name')
                if target_manifest:
                    if manifest_name == target_manifest:
                        manifests.append(document)
                else:
                    manifests.append(document)
        return charts, groups, manifests

    def find_chart_document(self, name):
        """Returns a chart document with the specified name

        :param str name: name of the desired chart document
        :returns: The requested chart document
        :rtype: dict
        :raises ManifestException: If a chart document with the
            specified name is not found
        """
        for chart in self.charts:
            if chart.get('metadata', {}).get('name') == name:
                return chart
        raise exceptions.BuildChartException(
            details='Could not find {} named "{}"'.format(
                schema.TYPE_CHART, name))

    def find_chart_group_document(self, name):
        """Returns a chart group document with the specified name

        :param str name: name of the desired chart group document
        :returns: The requested chart group document
        :rtype: dict
        :raises ManifestException: If a chart
            group document with the specified name is not found
        """
        for group in self.groups:
            if group.get('metadata', {}).get('name') == name:
                return group
        raise exceptions.BuildChartGroupException(
            details='Could not find {} named "{}"'.format(
                schema.TYPE_CHARTGROUP, name))

    def _build_chart_deps(self, chart):
        """Recursively build chart dependencies for ``chart``.

        :param dict chart: The chart whose dependencies will be recursively
            built.
        :returns: The chart with all dependencies.
        :rtype: dict
        :raises ManifestException: If a chart for a dependency name listed
            under ``chart['data']['dependencies']`` could not be found.
        """
        try:
            chart_dependencies = chart.get(const.KEYWORD_DATA, {}).get(
                'dependencies', [])
            for iter, dep in enumerate(chart_dependencies):
                if isinstance(dep, dict):
                    continue
                chart_dep = self.find_chart_document(dep)
                self._build_chart_deps(chart_dep)
                chart[const.KEYWORD_DATA]['dependencies'][iter] = chart_dep
        except Exception:
            raise exceptions.ChartDependencyException(
                details='Could not build dependencies for {} named "{}"'.
                format(schema.TYPE_CHART,
                       chart.get('metadata').get('name')))
        else:
            return chart

    def _build_chart_group(self, chart_group):
        """Builds the chart dependencies for`charts`chart group``.

        :param dict chart_group: The chart_group whose dependencies
            will be built.
        :returns: The chart_group with all dependencies.
        :rtype: dict
        :raises ManifestException: If a chart for a dependency name listed
            under ``chart_group['data']['chart_group']`` could not be found.
        """
        try:
            chart = None
            for iter, chart in enumerate(
                    chart_group.get(const.KEYWORD_DATA).get(
                        const.KEYWORD_CHARTS, [])):
                if isinstance(chart, dict):
                    continue
                chart_object = self.find_chart_document(chart)
                self._build_chart_deps(chart_object)
                chart_group[const.KEYWORD_DATA][const.KEYWORD_CHARTS][iter] = \
                    chart_object
        except exceptions.ManifestException:
            cg_name = chart_group.get('metadata', {}).get('name')
            raise exceptions.BuildChartGroupException(
                details='Could not build {} named "{}"'.format(
                    schema.TYPE_CHARTGROUP, cg_name))

        return chart_group

    def _build_armada_manifest(self):
        """Builds the Armada manifest while pulling out data
        from the chart_group.

        :returns: The Armada manifest with the data of the chart groups.
        :rtype: dict
        :raises ManifestException: If a chart group's data listed
            under ``chart_group[const.KEYWORD_DATA]`` could not be found.
        """
        for iter, group in enumerate(
                self.manifest.get(const.KEYWORD_DATA, {}).get(
                    const.KEYWORD_GROUPS, [])):
            if isinstance(group, dict):
                continue
            chart_grp = self.find_chart_group_document(group)
            self._build_chart_group(chart_grp)

            self.manifest[const.KEYWORD_DATA][const.KEYWORD_GROUPS][iter] = \
                chart_grp

        return self.manifest

    def get_manifest(self):
        """Builds the Armada manifest

        :returns: The Armada manifest.
        :rtype: dict
        """
        self._build_armada_manifest()

        return self.manifest

class ManifestHelper(Manifest):

    def __init__(self, documents, target_manifest=None):
        super(ManifestHelper, self).__init__(documents, target_manifest)

    def get_chart_group_documents(self):
        """Retrieve a list of documents corresponding to the chart groups
        listed in the selected/targeted manifest (self.manifest).

        A chart group document contains a metadata and data section.

        :returns: List of chart group documents
        :rtype: list
        """
        return self.manifest.get(const.KEYWORD_DATA).get(const.KEYWORD_GROUPS)

    def get_chart_groups(self):
        """Retrieve a list of chart group dictionaries corresponding to the
        chart groups listed in the selected/targeted manifest (self.manifest).

        A chart group dictionary is the data section of a chart group document.

        :returns: List of chart groups dictionaries
        :rtype: list
        """
        group_names = self.get_chart_group_documents()
        return [group.get(const.KEYWORD_DATA) for group in group_names]

    def get_chart_documents(self):
        """Retrieve a list of documents corresponding to the charts listed in
        all chart groups listed in the selected/targeted manifest
        (self.manifest).

        A chart document contains a metadata and data section.

        :returns: List of chart documents
        :rtype: list
        """
        charts = list()
        for group in self.get_chart_groups():
            charts.extend(group.get(const.KEYWORD_CHARTS))

        return charts

    def get_charts(self):
        """Retrieve a list of chart dictionaries corresponding to the charts
        listed in all chart groups listed in the selected/targeted manifest.

        A chart dictionary is the data section of a chart document.

        :returns: List of charts that belong to the manifest.
        :rtype: list
        """
        chart_documents = self.get_chart_documents()
        return [chart.get(const.KEYWORD_DATA) for chart in chart_documents]

    def get_release_prefix(self):
        """Retrieve the release prefix of the selected/targeted manifest.

        :returns: Release prefix
        :rtype: str
        """
        manifest_data = self.manifest.get(const.KEYWORD_DATA)
        return manifest_data.get(const.KEYWORD_PREFIX)
