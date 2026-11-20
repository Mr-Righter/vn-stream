//! Standalone data-only decoder. It never loads game scripts, plugins, or fonts.
use std::{
    env,
    fs::File,
    io::{Read, Seek, SeekFrom},
    process::ExitCode,
};

fn run() -> Result<(), Box<dyn std::error::Error>> {
    // Linux resource limits apply before parsing any untrusted image bytes.
    unsafe {
        let memory = libc::rlimit {
            rlim_cur: 512 * 1024 * 1024,
            rlim_max: 512 * 1024 * 1024,
        };
        let cpu = libc::rlimit {
            rlim_cur: 14,
            rlim_max: 14,
        };
        if libc::setrlimit(libc::RLIMIT_AS, &memory) != 0
            || libc::setrlimit(libc::RLIMIT_CPU, &cpu) != 0
        {
            return Err("resource limit unavailable".into());
        }
    }
    let args: Vec<_> = env::args_os().collect();
    if args.len() != 3 {
        return Err("expected input and output".into());
    }
    let mut input = File::open(&args[1])?;
    if input.metadata()?.len() > 64 * 1024 * 1024 {
        return Err("input size limit".into());
    }
    let mut header = [0u8; 32];
    input.read_exact(&mut header)?;
    let offset = if header.starts_with(b"TLG0.0\0sds\x1a") {
        15
    } else {
        0
    };
    input.seek(SeekFrom::Start(offset))?;
    input.read_exact(&mut header)?;
    let start = if header.starts_with(b"TLG5.0\0raw\x1a") {
        12
    } else if header.starts_with(b"TLG6.0\0raw\x1a") {
        15
    } else {
        return Err("unsupported TLG header".into());
    };
    let width = u32::from_le_bytes(header[start..start + 4].try_into()?);
    let height = u32::from_le_bytes(header[start + 4..start + 8].try_into()?);
    if width == 0 || height == 0 || u64::from(width) * u64::from(height) > 40_000_000 {
        return Err("pixel limit".into());
    }
    input.rewind()?;
    let mut image = libtlg_rs::load_tlg(&mut input)?;
    if image.width != width || image.height != height {
        return Err("geometry mismatch".into());
    }
    let (color, channels) = match image.color {
        libtlg_rs::TlgColorType::Bgra32 => (png::ColorType::Rgba, 4),
        libtlg_rs::TlgColorType::Bgr24 => (png::ColorType::Rgb, 3),
        libtlg_rs::TlgColorType::Grayscale8 => (png::ColorType::Grayscale, 1),
    };
    if channels > 1 {
        for pixel in image.data.chunks_exact_mut(channels) {
            pixel.swap(0, 2);
        }
    }
    let mut encoder = png::Encoder::new(File::create(&args[2])?, width, height);
    encoder.set_color(color);
    encoder.set_depth(png::BitDepth::Eight);
    encoder.write_header()?.write_image_data(&image.data)?;
    Ok(())
}

fn main() -> ExitCode {
    match run() {
        Ok(()) => ExitCode::SUCCESS,
        Err(error) => {
            eprintln!("TLG decode failed: {error}");
            ExitCode::FAILURE
        }
    }
}
