//! winpodx reverse-open shim — Windows-side stub.
//!
//! Invoked by Windows Explorer when the user picks a Linux app from
//! the right-click "Open with…" menu. Single responsibility: parse
//! its own filename + argv, write a JSON request to the host
//! listener's incoming directory (atomic .tmp + rename), exit.
//!
//! ## Per-slug identity from filename
//!
//! The same binary is hard-linked under N filenames
//! (`winpodx-<slug>.exe` for each app). The shim discovers its slug
//! by inspecting `argv[0]` / `current_exe()` — stripping the
//! `winpodx-` prefix and `.exe` suffix gives the slug to embed in
//! the request. Sharing one inode keeps the on-disk footprint flat
//! regardless of how many Linux apps are registered.
//!
//! ## No console flash
//!
//! `#![windows_subsystem = "windows"]` flips the PE subsystem to GUI
//! so launching the shim does NOT spawn a console window. The earlier
//! `.cmd` / `.vbs` wrappers were workarounds for the same problem;
//! Rust + `windows_subsystem` is the canonical fix.
//!
//! ## Atomic write
//!
//! Request files land at `\\tsclient\home\.local\share\winpodx\
//! reverse-open\incoming\<uuid>.json.tmp` and are renamed to
//! `<uuid>.json` only after the bytes are flushed to disk. The host
//! listener only matches files named `<uuid>.json` (not `.tmp`), so a
//! partial write can't trigger a spawn.

#![windows_subsystem = "windows"]

use std::env;
use std::fs;
use std::path::PathBuf;
use std::process;
use std::time::{SystemTime, UNIX_EPOCH};

/// Where the host's reverse-open listener watches for request files.
/// Reachable from the guest via the FreeRDP `+home-drive` redirect.
const INCOMING_DIR_UNC: &str =
    r"\\tsclient\home\.local\share\winpodx\reverse-open\incoming";

/// Filename prefix and suffix the slug-from-filename parser expects.
const SLUG_FILENAME_PREFIX: &str = "winpodx-";
const SLUG_FILENAME_SUFFIX: &str = ".exe";

/// Exit codes — kept in sync with the integration tests that check
/// the shim's behaviour on bad input.
const EXIT_BAD_ARGS: i32 = 2;
const EXIT_WRITE_FAIL: i32 = 3;

fn main() {
    let slug = match extract_slug() {
        Some(s) => s,
        None => process::exit(EXIT_BAD_ARGS),
    };

    let args: Vec<String> = env::args().collect();
    let file_arg = match args.get(1) {
        Some(s) if !s.is_empty() => s.clone(),
        _ => process::exit(EXIT_BAD_ARGS),
    };

    let unc = local_to_unc(&file_arg);

    let uid = new_uuid();
    let ts = format!(
        "{}",
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|d| d.as_secs())
            .unwrap_or(0)
    );

    let json = build_request_json(&slug, &unc, &ts);

    let tmp = format!("{}\\{}.json.tmp", INCOMING_DIR_UNC, uid);
    let final_path = format!("{}\\{}.json", INCOMING_DIR_UNC, uid);

    if fs::write(&tmp, json.as_bytes()).is_err() {
        process::exit(EXIT_WRITE_FAIL);
    }
    if fs::rename(&tmp, &final_path).is_err() {
        let _ = fs::remove_file(&tmp);
        process::exit(EXIT_WRITE_FAIL);
    }
}

/// Read the slug off the binary's own filename.
///
/// Returns `None` if the binary wasn't named per the expected
/// `winpodx-<slug>.exe` convention. Defensive — without the
/// prefix/suffix we'd send an empty `app` field that the host
/// listener rejects at schema validation anyway, but we prefer to
/// fail loudly on the guest with a non-zero exit.
fn extract_slug() -> Option<String> {
    let exe: PathBuf = env::current_exe().ok()?;
    let base = exe.file_name()?.to_str()?.to_lowercase();
    let stem = base
        .strip_prefix(SLUG_FILENAME_PREFIX)?
        .strip_suffix(SLUG_FILENAME_SUFFIX)?;
    if stem.is_empty() || stem == "reverse-open-shim" {
        // Refuse to fire when the shim is invoked by its bare
        // filename — that's the source binary copy in
        // `bin\winpodx-reverse-open-shim.exe`, not a per-app
        // hard link, so there's no slug to attach.
        return None;
    }
    Some(stem.to_string())
}

/// Translate a Windows local file path to its `\\tsclient\home\…` UNC
/// equivalent (the host's `$HOME` shared via FreeRDP's `+home-drive`).
///
/// Already-UNC paths pass through. A drive-relative path like
/// `Z:\Users\User\Documents\foo.txt` becomes
/// `\\tsclient\home\Users\User\Documents\foo.txt`. Any other shape
/// passes through verbatim — the host listener will reject it at
/// `safe_open_unc` validation if it isn't reachable under one of the
/// active share roots.
fn local_to_unc(file: &str) -> String {
    if file.starts_with(r"\\") {
        return file.to_string();
    }
    let bytes = file.as_bytes();
    if bytes.len() >= 3
        && bytes[1] == b':'
        && (bytes[2] == b'\\' || bytes[2] == b'/')
    {
        let rest = &file[3..];
        let rest_winpath = rest.replace('/', r"\");
        format!(r"\\tsclient\home\{}", rest_winpath)
    } else {
        file.to_string()
    }
}

/// Generate a UUIDv4 hex string without dashes.
///
/// Sources 16 random bytes via the OS RNG (BCryptGenRandom on
/// Windows). Stamps the version (0b0100 in byte 6) and variant
/// (0b10 in byte 8) nibbles per RFC 4122, then formats as 32 lower-
/// case hex chars. The listener's regex
/// (`[0-9a-fA-F-]{8,64}.json`) accepts dash-free or dashed forms.
fn new_uuid() -> String {
    let mut bytes = [0u8; 16];
    getrandom::getrandom(&mut bytes).expect("getrandom() failed");
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    let mut out = String::with_capacity(32);
    for b in bytes.iter() {
        out.push_str(&format!("{:02x}", b));
    }
    out
}

/// Build the request JSON payload the host listener expects.
///
/// Schema (kept in sync with
/// `winpodx.reverse_open.listener._validate_schema`):
///   version: 1
///   app:     <slug>      (^[a-z0-9-]+$)
///   path:    <unc>       (string, ≤ 4 KB, NUL-free, \\tsclient\… prefix)
///   ts:      <ts>        (string, non-empty)
///   pod_id:  null
fn build_request_json(slug: &str, path: &str, ts: &str) -> String {
    format!(
        r#"{{"version":1,"app":"{}","path":"{}","ts":"{}","pod_id":null}}"#,
        json_escape(slug),
        json_escape(path),
        json_escape(ts),
    )
}

/// Minimal JSON string escaper — enough for the three fields we
/// emit (slug regex doesn't include control chars, path may have
/// backslashes the JSON consumer needs to see literally).
fn json_escape(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    for c in s.chars() {
        match c {
            '\\' => out.push_str("\\\\"),
            '"' => out.push_str("\\\""),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if (c as u32) < 0x20 => {
                out.push_str(&format!("\\u{:04x}", c as u32));
            }
            c => out.push(c),
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn local_to_unc_passes_through_existing_unc() {
        assert_eq!(local_to_unc(r"\\server\share\x"), r"\\server\share\x");
    }

    #[test]
    fn local_to_unc_maps_drive_letter_to_tsclient_home() {
        assert_eq!(
            local_to_unc(r"C:\Users\User\notes.txt"),
            r"\\tsclient\home\Users\User\notes.txt"
        );
    }

    #[test]
    fn local_to_unc_normalises_forward_slashes() {
        assert_eq!(
            local_to_unc("Z:/User/x"),
            r"\\tsclient\home\User\x"
        );
    }

    #[test]
    fn json_escape_handles_metacharacters() {
        assert_eq!(json_escape(r#"a"b\c"#), "a\\\"b\\\\c");
    }

    #[test]
    fn new_uuid_is_32_hex_chars() {
        let u = new_uuid();
        assert_eq!(u.len(), 32);
        assert!(u.chars().all(|c| c.is_ascii_hexdigit()));
    }

    #[test]
    fn new_uuid_marks_v4_variant_bits() {
        let u = new_uuid();
        // Char index 12 = nibble (byte 6 high nibble) should be '4'.
        assert_eq!(u.chars().nth(12), Some('4'));
        // Char index 16 = nibble (byte 8 high nibble) should be 8|9|a|b.
        let v = u.chars().nth(16).unwrap();
        assert!("89ab".contains(v), "variant nibble was {}", v);
    }
}
