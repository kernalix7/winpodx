// SPDX-License-Identifier: MIT
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
const INCOMING_DIR_UNC: &str = r"\\tsclient\home\.local\share\winpodx\reverse-open\incoming";

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
    let (path, origin) = match args.get(1) {
        Some(s) if !s.is_empty() => classify_path(s),
        // No file argument: the user launched the app directly from its
        // "Linux Apps" Start-Menu / Desktop shortcut rather than via the
        // "Open with…" menu on a file. Emit a launch-only request (empty
        // path, origin "launch") so the host runs the Linux app with no
        // file argument instead of the shim silently exiting (#616).
        _ => (String::new(), "launch"),
    };

    let uid = new_uuid();
    let ts = format!(
        "{}",
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|d| d.as_secs())
            .unwrap_or(0)
    );

    let json = build_request_json(&slug, &path, origin, &ts);

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

/// Classify the file argument and produce the `(path, origin)` pair the
/// host listener consumes.
///
/// - A `\\…` UNC path (in practice `\\tsclient\home\…`) is a **host**
///   file reached through FreeRDP's `+home-drive` redirect. Passed
///   through verbatim with origin `"host"`; the host resolves it to its
///   `$HOME`-relative path.
/// - A drive-letter path (`C:\…`, `D:/…`) is a file on the **guest's own
///   disk**. The guest disk is exposed to the host as a network mount, so
///   the original Windows path is sent verbatim with origin `"guest"` and
///   the host maps it onto that mount.
/// - Any other shape passes through as `"host"`; the host validator
///   rejects it if it isn't reachable under an active share root.
///
/// Earlier versions rewrote *every* drive letter to `\\tsclient\home\…`,
/// which only makes sense if `$HOME` were the root of that drive. It never
/// is for `C:`, so a guest-local file (e.g. the Windows Desktop) resolved
/// to a non-existent host path and silently failed (#616).
fn classify_path(file: &str) -> (String, &'static str) {
    if file.starts_with(r"\\") {
        return (file.to_string(), "host");
    }
    let bytes = file.as_bytes();
    if bytes.len() >= 3 && bytes[1] == b':' && (bytes[2] == b'\\' || bytes[2] == b'/') {
        // Guest-local drive path — normalise separators, keep the drive.
        return (file.replace('/', r"\"), "guest");
    }
    (file.to_string(), "host")
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
///   version: 2
///   app:     <slug>      (^[a-z0-9-]+$)
///   path:    <path>      (string, ≤ 4 KB, NUL-free; empty for origin "launch")
///   origin:  "host"|"guest"|"launch"  (host = \\tsclient\… redirect;
///            guest = guest disk; launch = run the app with no file)
///   ts:      <ts>        (string, non-empty)
///   pod_id:  null
fn build_request_json(slug: &str, path: &str, origin: &str, ts: &str) -> String {
    format!(
        r#"{{"version":2,"app":"{}","path":"{}","origin":"{}","ts":"{}","pod_id":null}}"#,
        json_escape(slug),
        json_escape(path),
        json_escape(origin),
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
    fn classify_unc_is_host() {
        assert_eq!(
            classify_path(r"\\tsclient\home\Documents\x"),
            (r"\\tsclient\home\Documents\x".to_string(), "host")
        );
    }

    #[test]
    fn classify_drive_letter_is_guest_local() {
        // The Windows path is kept verbatim (NOT rewritten to
        // \\tsclient\home) and tagged guest so the host maps it onto
        // the guest-disk mount. Regression guard for #616.
        assert_eq!(
            classify_path(r"C:\Users\User\Desktop\notes.txt"),
            (r"C:\Users\User\Desktop\notes.txt".to_string(), "guest")
        );
    }

    #[test]
    fn classify_normalises_forward_slashes_keeping_drive() {
        assert_eq!(
            classify_path("D:/User/x"),
            (r"D:\User\x".to_string(), "guest")
        );
    }

    #[test]
    fn build_request_json_emits_v2_with_origin() {
        let j = build_request_json("sublime", r"C:\a\b.txt", "guest", "123");
        assert!(j.contains(r#""version":2"#));
        assert!(j.contains(r#""origin":"guest""#));
        assert!(j.contains(r#""app":"sublime""#));
        // Backslashes are JSON-escaped.
        assert!(j.contains(r#""path":"C:\\a\\b.txt""#));
    }

    #[test]
    fn build_request_json_launch_only_empty_path() {
        // #616: launching an app with no file emits origin "launch" and an
        // empty path; the host runs the app with no file argument.
        let j = build_request_json("sublime", "", "launch", "123");
        assert!(j.contains(r#""origin":"launch""#));
        assert!(j.contains(r#""app":"sublime""#));
        assert!(j.contains(r#""path":"""#));
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
