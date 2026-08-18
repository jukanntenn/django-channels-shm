fn main() {
    pyo3_build_config::use_pyo3_cfgs();
    // Plain `cargo test` / `cargo bench` link libpython dynamically, and
    // uv/pyenv-managed interpreters install it outside ldconfig's search
    // path, so the built binaries cannot load it at runtime (exit 127).
    // Embed the resolved lib dir as rpath so the artifacts find the exact
    // libpython they were linked against, regardless of the invoking shell.
    // Wheels are unaffected: maturin builds with `extension-module`, which
    // skips both the libpython link and this link-arg.
    if std::env::var_os("CARGO_FEATURE_EXTENSION_MODULE").is_none() {
        if let Some(lib_dir) = pyo3_build_config::get().lib_dir() {
            println!("cargo:rustc-link-arg=-Wl,-rpath,{lib_dir}");
        }
    }
}
