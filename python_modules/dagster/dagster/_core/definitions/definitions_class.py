from typing import TYPE_CHECKING, Any, Dict, Iterable, Mapping, Optional, Type, Union

import dagster._check as check
from dagster._annotations import experimental, public
from dagster._core.definitions.events import CoercibleToAssetKey
from dagster._core.execution.with_resources import with_resources
from dagster._core.instance import DagsterInstance
from dagster._utils.cached_method import cached_method

from .assets import AssetsDefinition, SourceAsset
from .cacheable_assets import CacheableAssetsDefinition
from .decorators import repository
from .job_definition import JobDefinition
from .repository_definition import (
    SINGLETON_REPOSITORY_NAME,
    PendingRepositoryDefinition,
    RepositoryDefinition,
)
from .resource_definition import ResourceDefinition
from .schedule_definition import ScheduleDefinition
from .sensor_definition import SensorDefinition
from .unresolved_asset_job_definition import UnresolvedAssetJobDefinition

if TYPE_CHECKING:
    from dagster._core.storage.asset_value_loader import AssetValueLoader


@experimental
class Definitions:
    def __init__(
        self,
        assets: Optional[
            Iterable[Union[AssetsDefinition, SourceAsset, CacheableAssetsDefinition]]
        ] = None,
        schedules: Optional[Iterable[ScheduleDefinition]] = None,
        sensors: Optional[Iterable[SensorDefinition]] = None,
        jobs: Optional[Iterable[Union[JobDefinition, UnresolvedAssetJobDefinition]]] = None,
        resources: Optional[Mapping[str, Any]] = None,
    ):
        """
        Example usage:

        defs = Definitions(
            assets=[asset_one, asset_two],
            schedules=[a_schedule],
            sensors=[a_sensor],
            jobs=[a_job],
            resources={
                "a_resource": some_resource,
            }
        )

        Create a set of definitions explicitly available and loadable by dagster tools.

        Dagster separates user-defined code from system tools such the web server and
        the daemon. Rather than loading code directly into process, a tool such as the
        webserver interacts with user-defined code over a serialization boundary.

        These tools must be able to locate and load this code when they start. Via CLI
        arguments or config, they specify a python module to inspect.

        A python module is loadable by dagster tools if it there is a top level variable
        named `defs` that is an instance of Definitions.

        Definitions provides a few conveniences for dealing with resources that do not apply to
        vanilla dagster definitions:

        (1) It takes a dictionary of top-level resources which are automatically bound
        (via with_resources) to any asset passed to it. If you need to apply different
        resources to different assets, use legacy @repository and use with_resources as before.

        (2) The resources dictionary takes raw python objects, not just resource definitions.
        """

        if assets:
            check.iterable_param(
                assets, "assets", (AssetsDefinition, SourceAsset, CacheableAssetsDefinition)
            )

        if schedules:
            check.iterable_param(schedules, "schedules", ScheduleDefinition)

        if sensors:
            check.iterable_param(sensors, "sensors", SensorDefinition)

        if jobs:
            check.iterable_param(jobs, "jobs", (JobDefinition, UnresolvedAssetJobDefinition))

        if resources:
            check.mapping_param(resources, "resources", key_type=str)

        resource_defs = coerce_resources_to_defs(resources or {})

        @repository(name=SINGLETON_REPOSITORY_NAME)
        def created_repo():
            return [
                *with_resources(assets or [], resource_defs),
                *(schedules or []),
                *(sensors or []),
                *(jobs or []),
            ]

        self._created_pending_or_normal_repo = created_repo

    @public
    def get_job_def(self, name: str) -> JobDefinition:
        """Get a job definition by name. If you passed in a an UnresolvedAssetJobDefinition
        (return value of define_asset_job) it will be resolved to a JobDefinition when returned
        from this function."""

        check.str_param(name, "name")
        return self.get_repository_def().get_job(name)

    @public
    def get_sensor_def(self, name: str) -> SensorDefinition:
        """Get a sensor definition name"""
        check.str_param(name, "name")
        return self.get_repository_def().get_sensor_def(name)

    @public
    def get_schedule_def(self, name: str) -> ScheduleDefinition:
        check.str_param(name, "name")
        return self.get_repository_def().get_schedule_def(name)

    @public
    def load_asset_value(
        self,
        asset_key: CoercibleToAssetKey,
        *,
        python_type: Optional[Type] = None,
        instance: Optional[DagsterInstance] = None,
        partition_key: Optional[str] = None,
    ) -> object:
        """
        Loads the contents of an asset as a Python object.

        Invokes `load_input` on the :py:class:`IOManager` associated with the asset.

        If you want to load the values of multiple assets, it's more efficient to use
        :py:meth:`~dagster.Definitions.get_asset_value_loader`, which avoids spinning up
        resources separately for each asset.

        Args:
            asset_key (Union[AssetKey, Sequence[str], str]): The key of the asset to load.
            python_type (Optional[Type]): The python type to load the asset as. This is what will
                be returned inside `load_input` by `context.dagster_type.typing_type`.
            partition_key (Optional[str]): The partition of the asset to load.

        Returns:
            The contents of an asset as a Python object.
        """
        return self.get_repository_def().load_asset_value(
            asset_key=asset_key,
            python_type=python_type,
            instance=instance,
            partition_key=partition_key,
        )

    @public
    def get_asset_value_loader(
        self, instance: Optional[DagsterInstance] = None
    ) -> "AssetValueLoader":
        """
        Returns an object that can load the contents of assets as Python objects.

        Invokes `load_input` on the :py:class:`IOManager` associated with the assets. Avoids
        spinning up resources separately for each asset.

        Usage:

            .. code-block:: python

                with defs.get_asset_value_loader() as loader:
                    asset1 = loader.load_asset_value("asset1")
                    asset2 = loader.load_asset_value("asset2")

        """
        return self.get_repository_def().get_asset_value_loader(
            instance=instance,
        )

    @cached_method
    def get_repository_def(self) -> RepositoryDefinition:
        return (
            self._created_pending_or_normal_repo.compute_repository_definition()
            if isinstance(self._created_pending_or_normal_repo, PendingRepositoryDefinition)
            else self._created_pending_or_normal_repo
        )

    def get_inner_repository_for_loading_process(
        self,
    ) -> Union[RepositoryDefinition, PendingRepositoryDefinition]:
        """This method is used internally to access the inner repository during the loading process
        at CLI entry points. We explicitly do not want to resolve the pending repo because the entire
        point is to defer that resolution until later."""
        return self._created_pending_or_normal_repo


def coerce_resources_to_defs(resources: Mapping[str, Any]) -> Dict[str, ResourceDefinition]:
    resource_defs = {}
    for key, resource_obj in resources.items():
        resource_defs[key] = (
            resource_obj
            if isinstance(resource_obj, ResourceDefinition)
            else ResourceDefinition.hardcoded_resource(resource_obj)
        )
    return resource_defs
