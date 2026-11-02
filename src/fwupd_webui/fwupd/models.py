from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field, field_validator

# fwupd's JSON is not a stability-guaranteed API. `extra="ignore"` means a fwupd
# release that adds keys cannot break us; leaving DeviceId/Name/Version required
# means a release that *removes* them fails loudly instead of silently producing
# empty rows in the UI.
_MODEL_CONFIG = ConfigDict(extra="ignore", populate_by_name=True)

_SHA256_RE = re.compile(r"[0-9a-fA-F]{64}")

# How firmware vendors label a Platform Key that was only ever meant for testing,
# as in "DO NOT TRUST - AMI Test PK".
_TEST_KEY_MARKERS = ("DO NOT TRUST", "DO NOT SHIP")


class Release(BaseModel):
    model_config = _MODEL_CONFIG

    version: str = Field(alias="Version")
    appstream_id: str | None = Field(default=None, alias="AppstreamId")
    variant: str | None = Field(default=None, alias="NameVariantSuffix")
    remote_id: str | None = Field(default=None, alias="RemoteId")
    summary: str | None = Field(default=None, alias="Summary")
    description: str | None = Field(default=None, alias="Description")
    urgency: str | None = Field(default=None, alias="Urgency")
    created: int | None = Field(default=None, alias="Created")
    locations: list[str] = Field(default_factory=list, alias="Locations")
    checksums: list[str] = Field(default_factory=list, alias="Checksum")
    size: int | None = Field(default=None, alias="Size")
    vendor: str | None = Field(default=None, alias="Vendor")
    flags: list[str] = Field(default_factory=list, alias="Flags")

    @field_validator("locations", "checksums", mode="before")
    @classmethod
    def _one_value_is_a_list(cls, value: object) -> object:
        """An optional field changing shape must not take down the whole page."""
        return [value] if isinstance(value, str) else value

    @property
    def download_url(self) -> str | None:
        """The first HTTPS location. fwupd also reports relative paths for local
        remotes, which cannot be downloaded."""
        return next((u for u in self.locations if u.startswith("https://")), None)

    @property
    def sha256(self) -> str | None:
        """The SHA-256 of the cab. fwupd lists it unlabelled beside a SHA-1, so it
        is identified by length."""
        return next((c.lower() for c in self.checksums if _SHA256_RE.fullmatch(c)), None)


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
    def is_test_platform_key(self) -> bool:
        if self.plugin != "uefi_pk" or not self.name:
            return False
        name = self.name.upper()
        return any(marker in name for marker in _TEST_KEY_MARKERS)

    @property
    def updatable(self) -> bool:
        return "updatable" in self.flags


def parse_devices(payload: dict) -> list[Device]:
    """Parse a `fwupdtool --json get-devices` / `get-updates` payload."""
    if "Devices" not in payload:
        raise ValueError(f"payload has no 'Devices' key; got keys {sorted(payload)}")
    return [Device.model_validate(d) for d in payload["Devices"]]
