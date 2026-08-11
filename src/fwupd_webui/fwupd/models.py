from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

# fwupd's JSON is not a stability-guaranteed API. `extra="ignore"` means a fwupd
# release that adds keys cannot break us; leaving DeviceId/Name/Version required
# means a release that *removes* them fails loudly instead of silently producing
# empty rows in the UI.
_MODEL_CONFIG = ConfigDict(extra="ignore", populate_by_name=True)


class Release(BaseModel):
    model_config = _MODEL_CONFIG

    version: str = Field(alias="Version")
    appstream_id: str | None = Field(default=None, alias="AppstreamId")
    remote_id: str | None = Field(default=None, alias="RemoteId")
    summary: str | None = Field(default=None, alias="Summary")
    description: str | None = Field(default=None, alias="Description")
    urgency: str | None = Field(default=None, alias="Urgency")
    created: int | None = Field(default=None, alias="Created")
    uri: str | None = Field(default=None, alias="Uri")
    size: int | None = Field(default=None, alias="Size")
    vendor: str | None = Field(default=None, alias="Vendor")
    flags: list[str] = Field(default_factory=list, alias="Flags")


class Device(BaseModel):
    model_config = _MODEL_CONFIG

    device_id: str = Field(alias="DeviceId")
    # Name is genuinely optional: a linux_display monitor on real hardware
    # reports a DeviceId, GUID and serial but no Name at all. Requiring it took
    # down the whole page for one unnamed device.
    name: str | None = Field(default=None, alias="Name")
    vendor: str | None = Field(default=None, alias="Vendor")
    version: str | None = Field(default=None, alias="Version")
    plugin: str | None = Field(default=None, alias="Plugin")
    protocol: str | None = Field(default=None, alias="Protocol")
    summary: str | None = Field(default=None, alias="Summary")
    serial: str | None = Field(default=None, alias="Serial")
    parent_device_id: str | None = Field(default=None, alias="ParentDeviceId")
    guids: list[str] = Field(default_factory=list, alias="Guid")
    flags: list[str] = Field(default_factory=list, alias="Flags")
    releases: list[Release] = Field(default_factory=list, alias="Releases")

    @property
    def display_name(self) -> str:
        """Always safe to render. Use this, never `name`, in templates and sorts."""
        if self.name:
            return self.name
        if self.plugin:
            return f"Unknown {self.plugin} device"
        return "Unknown device"

    @property
    def updatable(self) -> bool:
        return "updatable" in self.flags


def parse_devices(payload: dict) -> list[Device]:
    """Parse a `fwupdtool --json get-devices` / `get-updates` payload."""
    if "Devices" not in payload:
        raise ValueError(f"payload has no 'Devices' key; got keys {sorted(payload)}")
    return [Device.model_validate(d) for d in payload["Devices"]]
