#!/usr/bin/env python3
"""
Setup & Fix Audio untuk Everest Semiconductor ES8336 (sof-essx8336)
Khususnya pada laptop Axioo Hype 10 (Intel Gemini Lake / Celeron N4020).

Fitur yang dikonfigurasi:
1. Menonaktifkan PCI Autosuspend / Runtime PM yang menyebabkan audio hilang setelah ~12 detik.
2. Mengatur ALSA UCM (HiFi.conf) agar internal speaker memiliki prioritas utama dan amp tidak bentrok.
3. Mengatur Modprobe quirk (0x40) dan mematikan power-saving audio.
4. Mengatur konfigurasi WirePlumber untuk mematikan session suspend-on-idle.
5. Mengaktifkan saklar Speaker, memaksimalkan DAC & Pre-amp mixer, dan menyimpan status ALSA.
"""

import os
import sys
import subprocess
import shutil
import pwd
from pathlib import Path

# Warna untuk output terminal
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
BLUE = "\033[94m"
RESET = "\033[0m"


def print_info(msg):
    print(f"{BLUE}[INFO]{RESET} {msg}")


def print_success(msg):
    print(f"{GREEN}[OK]{RESET} {msg}")


def print_warn(msg):
    print(f"{YELLOW}[PERINGATAN]{RESET} {msg}")


def print_error(msg):
    print(f"{RED}[ERROR]{RESET} {msg}")


def check_and_elevate_root():
    """Memastikan skrip dijalankan dengan akses root (sudo)."""
    if os.geteuid() != 0:
        print_info("Skrip membutuhkan hak akses root. Meminta sudo...")
        try:
            os.execvp("sudo", ["sudo", sys.executable] + sys.argv)
        except Exception as e:
            print_error(f"Gagal menjalankan sudo: {e}")
            sys.exit(1)


def get_real_user_info():
    """Mendapatkan user dan home directory pengguna asli (bukan root saat sudo)."""
    sudo_user = os.environ.get("SUDO_USER")
    if sudo_user:
        try:
            pw = pwd.getpwnam(sudo_user)
            return pw.pw_name, pw.pw_uid, pw.pw_gid, Path(pw.pw_dir)
        except KeyError:
            pass
    uid = os.getuid()
    pw = pwd.getpwuid(uid)
    return pw.pw_name, pw.pw_uid, pw.pw_gid, Path(pw.pw_dir)


def configure_modprobe():
    """Mengatur kernel module parameters di /etc/modprobe.d/."""
    print_info("1. Mengonfigurasi parameter kernel modprobe...")

    # Power-save disable
    powersave_conf = Path("/etc/modprobe.d/audio-disable-powersave.conf")
    powersave_content = "options snd_hda_intel power_save=0 power_save_controller=N\n"
    powersave_conf.write_text(powersave_content)
    print_success(f"Tersimpan: {powersave_conf}")

    # Quirk ES8336 (0x40: Inverted Jack Detection)
    es8336_conf = Path("/etc/modprobe.d/es8336.conf")
    es8336_content = "options snd_soc_sof_es8336 quirk=0x40\n"
    es8336_conf.write_text(es8336_content)
    print_success(f"Tersimpan: {es8336_conf}")


def configure_udev_autosuspend():
    """Membuat udev rule agar PCI audio controller tidak ditidurkan (runtime suspend)."""
    print_info("2. Mengonfigurasi Udev Rule untuk mencegah PCI Autosuspend...")

    udev_rule = Path("/etc/udev/rules.d/99-disable-audio-autosuspend.rules")
    rule_content = 'ACTION=="add", SUBSYSTEM=="pci", ATTR{vendor}=="0x8086", ATTR{device}=="0x3198", ATTR{power/control}="on"\n'
    udev_rule.write_text(rule_content)
    print_success(f"Tersimpan: {udev_rule}")

    # Reload udev
    subprocess.run(["udevadm", "control", "--reload-rules"], check=False)
    subprocess.run(["udevadm", "trigger"], check=False)

    # Terapkan langsung ke sysfs jika ada
    pci_power_path = Path("/sys/bus/pci/devices/0000:00:0e.0/power/control")
    if pci_power_path.exists():
        try:
            pci_power_path.write_text("on\n")
            print_success("Runtime power management langsung disetel ke 'on' (tidak pernah suspend).")
        except Exception as e:
            print_warn(f"Tidak dapat menulis ke sysfs pci control: {e}")


def configure_alsa_ucm():
    """Mengonfigurasi profil ALSA UCM HiFi.conf untuk sof-essx8336."""
    print_info("3. Mengonfigurasi ALSA UCM (HiFi.conf)...")

    ucm_dir = Path("/usr/share/alsa/ucm2/Intel/sof-essx8336")
    hifi_conf = ucm_dir / "HiFi.conf"

    if not ucm_dir.exists():
        print_warn(f"Folder UCM {ucm_dir} belum ditemukan. Membuat direktori...")
        ucm_dir.mkdir(parents=True, exist_ok=True)

    if hifi_conf.exists() and not (ucm_dir / "HiFi.conf.bak").exists():
        shutil.copy2(hifi_conf, ucm_dir / "HiFi.conf.bak")
        print_info("Membuat backup: HiFi.conf.bak")

    patched_hifi = """SectionVerb {
	EnableSequence [
		disdevall ""
		# Disable all inputs / outputs
		#   (may be duplicated with disdevall)
		cset "name='Headphone Switch' off"
		cset "name='Headset Mic Switch' off"
		cset "name='Internal Mic Switch' off"
		cset "name='DAC Mono Mix Switch' off"
		cset "name='Speaker Switch' on"
	]
}

If.amic {
	Condition {
		Type String
		Empty "${var:DeviceDmic}"
	}
	True.SectionDevice."Mic" {
		Comment "Analog Microphone"

		ConflictingDevice [
			"Headset"
		]

		EnableSequence [
			cset "name='Differential Mux' lin1-rin1"
			cset "name='Internal Mic Switch' on"
		]

		DisableSequence [
			cset "name='Internal Mic Switch' off"
		]

		Value {
			CapturePriority 100
			CapturePCM "hw:${CardId}"
			CaptureMixerElem "ADC PGA Gain"
			CaptureMasterElem "ADC"
		}
	}
}

If.dmic {
	Condition {
		Type String
		Empty "${var:DeviceDmic}"
	}
	False.SectionDevice."${var:DeviceDmic}" {
		Comment "Digital Microphone"

		Value {
			CapturePriority 100
			CapturePCM "hw:${CardId},1"
			If.chn {
				Condition {
					Type RegexMatch
					Regex "cfg-dmics:[34]"
					String "${CardComponents}"
				}
				True {
					CaptureChannels 4
				}
			}
			CaptureMixerElem "Dmic0"
			CaptureVolume "Dmic0 Capture Volume"
			CaptureSwitch "Dmic0 Capture Switch"
		}
	}
}

SectionDevice."Speaker" {
	Comment "Speakers"

	ConflictingDevice [
		"Headphones"
	]

	EnableSequence [
		cset "name='Speaker Switch' on"
	]

	DisableSequence [
	]

	Value {
		PlaybackPriority 300
		PlaybackPCM "hw:${CardId}"
		# The es8316 only has a HP-amp which is muxed to the speaker
		# or to the headpones output
		PlaybackMixerElem "Headphone Mixer"
		PlaybackMasterElem "DAC"
	}
}

SectionDevice."Headphones" {
	Comment "Headphones"

	ConflictingDevice [
		"Speaker"
	]

	EnableSequence [
		cset "name='Headphone Switch' on"
	]

	DisableSequence [
		cset "name='Headphone Switch' off"
	]

	Value {
		PlaybackPriority 100
		PlaybackPCM "hw:${CardId}"
		PlaybackMixerElem "Headphone Mixer"
		PlaybackMasterElem "DAC"
		# JackControl "Headphone Jack"
		# JackHWMute "Speaker"
	}
}

SectionDevice."Headset" {
	Comment "Headset Microphone"

	If.conflict {
		Condition {
			Type String
			Empty "${var:DeviceDmic}"
		}
		True.ConflictingDevice [
			"Mic"
		]
	}

	EnableSequence [
		cset "name='Headset Mic Switch' on"
		cset "name='Digital Mic Mux' 'dmic disable'"
	]

	DisableSequence [
		cset "name='Headset Mic Switch' off"
	]

	Value {
		CapturePriority 300
		CapturePCM "hw:${CardId}"
		CaptureMixerElem "ADC PGA Gain"
		CaptureMasterElem "ADC"
		JackControl "Headset Mic Jack"
	}
}

Include.hdmi.File "/Intel/sof-essx8336/Hdmi.conf"
"""
    hifi_conf.write_text(patched_hifi)
    print_success(f"Tersimpan: {hifi_conf}")

    # Muat ulang UCM jika soundcard aktif
    subprocess.run(["alsaucm", "-c", "sof-essx8336", "reload"], check=False)


def configure_wireplumber(user_name, uid, gid, user_home):
    """Mengonfigurasi WirePlumber untuk user agar tidak melakukan idle suspend."""
    print_info(f"4. Mengonfigurasi WirePlumber untuk user: {user_name}...")

    wp_dir = user_home / ".config" / "wireplumber" / "main.lua.d"
    wp_dir.mkdir(parents=True, exist_ok=True)

    lua_file = wp_dir / "51-alsa-disable-suspend.lua"
    lua_content = """alsa_monitor.properties["alsa.reserve"] = false

table.insert(alsa_monitor.rules, {
  matches = {
    {
      { "node.name", "matches", "alsa_output.*" },
    },
    {
      { "node.name", "matches", "alsa_input.*" },
    },
  },
  apply_properties = {
    ["session.suspend-timeout-seconds"] = 0,
    ["api.alsa.headroom"] = 1024,
    ["api.alsa.period-size"] = 1024,
  },
})
"""
    lua_file.write_text(lua_content)

    # Perbaiki kepemilikan file/folder ke user asli
    os.chown(lua_file, uid, gid)
    os.chown(wp_dir, uid, gid)
    os.chown(wp_dir.parent, uid, gid)
    print_success(f"Tersimpan: {lua_file}")


def apply_alsa_mixer_settings():
    """Mengaktifkan mixer controls pada chip ES8336 dan menyimpan state."""
    print_info("5. Mengatur ALSA mixer & volume hardware...")

    cmds = [
        ["amixer", "-c", "sofessx8336", "sset", "Speaker", "on"],
        ["amixer", "-c", "sofessx8336", "sset", "Headphone", "off"],
        ["amixer", "-c", "sofessx8336", "sset", "DAC", "100%"],
        ["amixer", "-c", "sofessx8336", "sset", "Headphone Mixer", "100%"],
        ["alsactl", "store"],
    ]

    for cmd in cmds:
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode == 0:
            print_success(f"Berhasil: {' '.join(cmd)}")
        else:
            print_warn(f"Perintah {' '.join(cmd)} keluar dengan kode {res.returncode}")


def apply_pipewire_user_settings(user_name):
    """Menyetel parameter PipeWire runtime suspend-on-idle dan volume 100%."""
    print_info("6. Menerapkan setting PipeWire runtime...")

    # Jalankan perintah user melalui su
    pw_cmds = [
        f"pw-metadata -n settings 0 node.suspend-on-idle false",
        f"pactl set-sink-volume @DEFAULT_SINK@ 100%",
        f"pactl set-sink-mute @DEFAULT_SINK@ 0",
    ]

    for c in pw_cmds:
        subprocess.run(["su", "-", user_name, "-c", c], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    print_success("Pengaturan PipeWire selesai diterapkan.")


def test_sound(user_name):
    """Tes pemutaran suara pendek."""
    wav_path = "/usr/share/sounds/alsa/Front_Center.wav"
    if Path(wav_path).exists():
        print_info("Memutar sampel suara tes...")
        subprocess.run(["su", "-", user_name, "-c", f"paplay {wav_path} || aplay {wav_path}"], check=False)


def main():
    print("=" * 60)
    print("  AUDIO SETUP SCRIPT UNTUK EVEREST ES8336 (AXIOO HYPE 10)  ")
    print("=" * 60)

    check_and_elevate_root()
    user_name, uid, gid, user_home = get_real_user_info()

    configure_modprobe()
    configure_udev_autosuspend()
    configure_alsa_ucm()
    configure_wireplumber(user_name, uid, gid, user_home)
    apply_alsa_mixer_settings()
    apply_pipewire_user_settings(user_name)

    print("\n" + "=" * 60)
    print_success("SEMUA KONFIGURASI AUDIO TELAH BERHASIL DIPASANG!")
    print("=" * 60)
    print_info("Catatan:")
    print("1. Jika laptop baru saja di-install ulang, silakan REBOOT komputer")
    print("   agar modul kernel dan quirk 0x40 termuat sempurna.")
    print("2. Pengaturan PCI Autosuspend dan UCM sekarang sudah permanen.")
    print("=" * 60)

    test_sound(user_name)


if __name__ == "__main__":
    main()
