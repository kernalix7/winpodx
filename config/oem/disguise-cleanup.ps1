# winpodx bare-metal disguise (#246): prune unused virtio driver service keys.
#
# The virtio-win bundle installs viostor / vioscsi / BalloonService even when
# the matching device is absent (a SATA system disk, no memory balloon). Bare
# existence of these HKLM\SYSTEM\...\Services\* keys is what al-khaser's KVM
# section flags as a VM tell. Remove ONLY the keys whose device is not present,
# so a guest that actually boots from virtio storage (or uses a balloon) is
# never left without its driver. Run once, post-install, by config/oem/install.bat.
$ErrorActionPreference = 'SilentlyContinue'

# virtio storage controllers present? blk = DEV_1001/1042, scsi = DEV_1004/1048.
$virtioStorage = Get-PnpDevice -PresentOnly | Where-Object {
    $_.InstanceId -match 'VEN_1AF4&DEV_(1001|1042|1004|1048)'
}
if (-not $virtioStorage) {
    Remove-Item 'HKLM:\SYSTEM\CurrentControlSet\Services\viostor' -Recurse -Force
    Remove-Item 'HKLM:\SYSTEM\CurrentControlSet\Services\vioscsi' -Recurse -Force
}

# virtio balloon present? DEV_1002 (legacy) / DEV_1045 (modern).
$virtioBalloon = Get-PnpDevice -PresentOnly | Where-Object {
    $_.InstanceId -match 'VEN_1AF4&DEV_(1002|1045)'
}
if (-not $virtioBalloon) {
    Remove-Item 'HKLM:\SYSTEM\CurrentControlSet\Services\BalloonService' -Recurse -Force
}

# Hyper-V integration drivers. al-khaser enumerates \Driver and \GLOBAL?? for
# vmbus / VMBusHID / vmgid / hyperkbd / HyperVideo / IndirectKmd (+ the VMBUS# /
# VmGenerationCounter / VmGid / VDRVROOT objects those drivers create). Under
# KVM the guest is NOT a Hyper-V guest, so these drivers serve nothing -- mark
# them disabled (Start=4) so they never load and create no driver/global object.
# GUARDED on HypervisorPresent=False so a genuine Hyper-V guest keeps VMBus.
if (-not (Get-CimInstance Win32_ComputerSystem).HypervisorPresent) {
    foreach ($svc in 'vmbus', 'VMBusHID', 'vmgid', 'hyperkbd', 'HyperVideo', 'IndirectKmd') {
        Set-ItemProperty "HKLM:\SYSTEM\CurrentControlSet\Services\$svc" -Name Start -Value 4 -ErrorAction SilentlyContinue
    }
}
