// SPDX-License-Identifier: MIT
//! Embed a Windows VERSIONINFO resource into the shim PE at build time.
//!
//! The reverse-open shim is a tiny, stripped, unsigned binary, which trips
//! AV ML heuristics as a false positive (#425). A metadata-less PE scores
//! worse than one carrying real publisher/product strings, so we stamp
//! CompanyName / ProductName / FileDescription / version into the binary.
//! It's not a substitute for code-signing, but it's a cheap legitimacy
//! signal and costs nothing at runtime.
//!
//! Resource embedding only applies to Windows targets; on any other target
//! this build script is a no-op. `winresource` shells out to `windres`
//! (mingw) or `llvm-rc` to compile the resource.

fn main() {
    // CARGO_CFG_TARGET_OS is "windows" for x86_64-pc-windows-{gnu,msvc}.
    if std::env::var("CARGO_CFG_TARGET_OS").as_deref() != Ok("windows") {
        return;
    }

    let mut res = winresource::WindowsResource::new();
    // FileVersion / ProductVersion are taken from CARGO_PKG_VERSION by
    // default; the string fields below are what an AV / the file's
    // Properties → Details tab will show.
    res.set("CompanyName", "Kim DaeHyun")
        .set("ProductName", "WinPodX")
        .set(
            "FileDescription",
            "WinPodX reverse-open shim — relays a Windows file-open back to the Linux host",
        )
        .set("InternalName", "winpodx-reverse-open-shim")
        .set("OriginalFilename", "winpodx-reverse-open-shim.exe")
        .set(
            "LegalCopyright",
            "Copyright (c) 2026 Kim DaeHyun. MIT licensed. https://github.com/kernalix7/winpodx",
        );

    if let Err(e) = res.compile() {
        // Don't hard-fail the build if the resource compiler is missing on a
        // contributor's machine — the binary still works, it just lacks the
        // metadata. CI / release builds (which have the toolchain) get the
        // resource; ad-hoc builds degrade gracefully.
        println!("cargo:warning=winresource: could not embed version metadata: {e}");
    }
}
