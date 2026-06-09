/*
 * winpodx bare-metal disguise (#246) -- synthetic WSMT data table.
 *
 * al-khaser's firmware_ACPI() flags the guest when the WSMT ("Windows SMM
 * Security Mitigations Table") is ABSENT -- QEMU never emits one. This injects
 * a valid WSMT (via -acpitable) advertising all three SMM mitigations set, as a
 * security-hardened bare-metal box reports.
 *
 * Compiled with iasl as a static data table; the OEM ID literal is
 * sed-substituted to the host vendor at image-build time (kept consistent with
 * the patched ACPI tables).
 *
 * Format: [ByteLength]  FieldName : HexFieldValue
 */
[0004]                          Signature : "WSMT"
[0004]                       Table Length : 00000028
[0001]                           Revision : 01
[0001]                           Checksum : 00
[0006]                             Oem ID : "ALASKA"
[0008]                       Oem Table ID : "A M I   "
[0004]                       Oem Revision : 00000001
[0004]                    Asl Compiler ID : "INTL"
[0004]              Asl Compiler Revision : 20200101

[0004]                   Protection Flags : 00000007
                       FIXED_COMM_BUFFERS : 1
        COMM_BUFFER_NESTED_PTR_PROTECTION : 1
               SYSTEM_RESOURCE_PROTECTION : 1
