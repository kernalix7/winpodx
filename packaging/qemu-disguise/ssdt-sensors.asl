/*
 * winpodx bare-metal disguise (#246) -- synthetic sensor SSDT.
 *
 * QEMU's guest exposes no ACPI thermal zone, so al-khaser's
 * MSAcpi_ThermalZoneTemperature / ThermalZoneInformation WMI probes return
 * nothing -- a VM tell. This SSDT adds one thermal zone reporting a plausible,
 * static CPU-package temperature so those probes succeed like a bare-metal box.
 *
 * Injected via QEMU `-acpitable sig=SSDT,file=...` (no recompile). The OEM ID is
 * replaced at build time with the host's vendor (kept consistent with the
 * patched ACPI tables); the placeholder below is a generic, non-VM default.
 *
 * Temps are tenths of a Kelvin: T(dK) = (celsius + 273.2) * 10.
 *   _TMP 3182 = 45.0 C   _CRT 3732 = 100.0 C   _PSV 3632 = 90.0 C
 */
DefinitionBlock ("", "SSDT", 2, "ALASKA", "WPSENSOR", 0x00000001)
{
    Scope (\_TZ)
    {
        ThermalZone (TZ0)
        {
            Name (_TZP, 0x00)          // read on demand, not polled
            Method (_TMP, 0, NotSerialized) { Return (0x0C6E) }   // 45.0 C
            Method (_PSV, 0, NotSerialized) { Return (0x0E30) }   // 90.0 C passive trip
            Method (_CRT, 0, NotSerialized) { Return (0x0E94) }   // 100.0 C critical
        }
    }
}
