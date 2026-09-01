//! Build script: embed the PyInstaller-bundled backend (dist/md-sync) into
//! the desktop binary so it runs without any Python installation.
//!
//! Probe order:
//!   1. $MD_SYNC_BACKEND (explicit path)
//!   2. <repo>/dist/md-sync[.exe]  (packaged automatically by scripts/build_desktop.py)
//!
//! If none is found the build still succeeds and emits a `None` constant;
//! at runtime the desktop app falls back to `python -m uvicorn`.

use std::env;
use std::path::PathBuf;

fn main() {
    println!("cargo:rerun-if-changed=build.rs");

    let target_os = env::var("CARGO_CFG_TARGET_OS").unwrap_or_default();
    let exe_name = if target_os == "windows" {
        "md-sync.exe"
    } else {
        "md-sync"
    };

    let explicit = env::var("MD_SYNC_BACKEND").ok().map(PathBuf::from);
    let repo_dist = {
        let manifest = PathBuf::from(env::var("CARGO_MANIFEST_DIR").unwrap());
        manifest.parent().unwrap().join("dist").join(exe_name)
    };

    let chosen = explicit
        .filter(|p| p.is_file())
        .or_else(|| repo_dist.is_file().then(|| repo_dist.clone()));

    let out = PathBuf::from(env::var("OUT_DIR").unwrap()).join("backend_embed.rs");

    match chosen {
        Some(path) => {
            println!("cargo:rerun-if-changed={}", path.display());
            let path_str = path.to_str().unwrap();
            let fname = path.file_name().unwrap().to_str().unwrap();
            std::fs::write(
                &out,
                format!(
                    "pub const BACKEND_EMBED: Option<(&[u8], &str)> = Some((include_bytes!({path_str:?}), {fname:?}));\n"
                ),
            )
            .expect("write backend_embed.rs");
            println!("cargo:warning=md-sync backend embedded from {}", path.display());
        }
        None => {
            std::fs::write(&out, "pub const BACKEND_EMBED: Option<(&[u8], &str)> = None;\n")
                .expect("write backend_embed.rs");
            println!(
                "cargo:warning=md-sync backend NOT embedded (dist/{exe_name} missing); desktop falls back to `python -m uvicorn`"
            );
        }
    }
}
