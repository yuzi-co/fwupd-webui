#!/usr/bin/env bash
source <(curl -fsSL https://raw.githubusercontent.com/community-scripts/ProxmoxVE/main/misc/build.func)
# Copyright (c) 2021-2026 community-scripts ORG
# Author: yuzi-co
# License: MIT | https://github.com/community-scripts/ProxmoxVE/raw/main/LICENSE
# Source: https://github.com/yuzi-co/fwupd-webui

APP="fwupd-webui"
var_tags="${var_tags:-monitoring;hardware}"
var_cpu="${var_cpu:-1}"
var_ram="${var_ram:-512}"
var_disk="${var_disk:-4}"
var_os="${var_os:-debian}"
var_version="${var_version:-13}"
var_arm64="${var_arm64:-no}"

# PRIVILEGED, deliberately. Enumerating firmware means issuing NVMe admin
# commands and SCSI generic ioctls against the host's disks; an unprivileged
# container sees only the CPU and display. Measured on real hardware: 8 devices
# privileged, 2 with every capability short of it.
#
# build.func's configure_usb_passthrough() runs for privileged containers and
# emits the device-cgroup access this needs, so no custom LXC config is required.
var_unprivileged="${var_unprivileged:-0}"

header_info "$APP"
variables
color
catch_errors

function update_script() {
  header_info
  check_container_storage
  check_container_resources

  if [[ ! -d /opt/fwupd-webui ]]; then
    msg_error "No ${APP} Installation Found!"
    exit
  fi

  msg_info "Updating $APP"
  systemctl stop fwupd-webui
  cd /opt/fwupd-webui/src-checkout || exit
  $STD git fetch origin main
  $STD git checkout FETCH_HEAD
  $STD /opt/fwupd-webui/venv/bin/pip install --upgrade /opt/fwupd-webui/src-checkout
  systemctl start fwupd-webui
  msg_ok "Updated $APP"
  exit
}

start
build_container
description

msg_ok "Completed Successfully!\n"
echo -e "${CREATING}${GN}${APP} setup has been successfully initialized!${CL}"
echo -e "${INFO}${YW} Access it using the following URL:${CL}"
echo -e "${TAB}${GATEWAY}${BGN}http://${IP}:8099${CL}"
echo -e "${INFO}${YW} Firmware flashing is DISABLED by default.${CL}"
echo -e "${TAB}${GATEWAY}${BGN}Set FWUPD_WEBUI_ENABLE_FLASHING=true in /etc/fwupd-webui/env to enable it.${CL}"
