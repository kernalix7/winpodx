/*
 * winpodx bare-metal disguise (#246) -- synthetic sensor + bare-metal-device SSDT.
 *
 * Injected via QEMU `-acpitable file=...` (no guest-OS recompile). Two jobs:
 *
 * 1. A thermal zone with a static, plausible CPU temperature, so al-khaser's
 *    MSAcpi_ThermalZoneTemperature / ThermalZoneInformation WMI probes succeed
 *    like a bare-metal box (they return nothing on a stock QEMU guest).
 *    Temps are tenths of a Kelvin: T(dK) = round((celsius + 273.2) * 10).
 *      _TMP 0x0C6E = 45.0 C   _PSV 0x0E30 = 90.0 C   _CRT 0x0E94 = 100.0 C
 *
 * 2. The fixed-feature PnP devices al-khaser's firmware_ACPI() scans the raw
 *    ACPI table bytes for and flags as MISSING on a VM: PNP0C0C (power button),
 *    PNP0C0E (sleep button), PNP0C14 (ACPI-WMI), PNP0D80 (power mgmt), PNP0000
 *    (PIC). They use STRING _HIDs (not packed EISAIDs) so the literal IDs land
 *    in the table bytes for the scan; Windows accepts string _HIDs fine.
 *
 * The OEM ID is replaced at image-build time with the host vendor (kept
 * consistent with the patched ACPI tables); the literal below is a generic
 * non-VM default that build_disguise_image's iasl step sed-substitutes.
 */
DefinitionBlock ("", "SSDT", 2, "ALASKA", "WPSENSOR", 0x00000001)
{
    Scope (\_TZ)
    {
        ThermalZone (TZ0)
        {
            Name (_TZP, 0x00)                                    // read on demand
            Method (_TMP, 0, NotSerialized) { Return (0x0C6E) }  // 45.0 C
            Method (_PSV, 0, NotSerialized) { Return (0x0E30) }  // 90.0 C passive
            Method (_CRT, 0, NotSerialized) { Return (0x0E94) }  // 100.0 C critical
        }
    }

    Scope (\_SB)
    {
        Device (PWRB) { Name (_HID, "PNP0C0C") Method (_STA) { Return (0x0F) } }  // power button
        Device (SLPB) { Name (_HID, "PNP0C0E") Method (_STA) { Return (0x0F) } }  // sleep button
        Device (WMIA) { Name (_HID, "PNP0C14") Method (_STA) { Return (0x0F) } }  // ACPI-WMI
        Device (PWMC) { Name (_HID, "PNP0D80") Method (_STA) { Return (0x0F) } }  // power mgmt
        Device (PIC0) { Name (_HID, "PNP0000") Method (_STA) { Return (0x0F) } }  // PIC
    }
}
