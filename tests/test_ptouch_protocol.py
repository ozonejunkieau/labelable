"""Tests for Brother P-Touch protocol parsing."""

import struct

import pytest

from labelable.printers.ptouch_protocol import (
    CMD_INITIALIZE,
    CMD_PRINT_AND_FEED,
    CMD_RESET,
    Error1,
    Error2,
    MediaKind,
    StatusType,
    build_mode_command,
    build_print_info,
    build_print_job,
    parse_status,
)


def _make_status_bytes(**overrides: int) -> bytes:
    """Build a 32-byte status response with optional byte overrides."""
    data = bytearray(32)
    for offset, value in overrides.items():
        data[int(offset)] = value
    return bytes(data)


class TestParseStatus:
    def test_all_zeros(self):
        status = parse_status(bytes(32))
        assert status.error1 == Error1.NONE
        assert status.error2 == Error2.NONE
        assert status.media_width_mm == 0
        assert status.media_kind == MediaKind.NO_MEDIA
        assert status.status_type == StatusType.REPLY
        assert not status.has_errors

    def test_valid_12mm_laminated(self):
        data = bytearray(32)
        data[10] = 12  # 12mm width
        data[11] = 0x01  # laminated tape
        data[18] = 0x00  # reply
        data[24] = 0x01  # white tape
        data[25] = 0x08  # black text
        status = parse_status(bytes(data))

        assert status.media_width_mm == 12
        assert status.media_kind == MediaKind.LAMINATED_TAPE
        assert status.status_type == StatusType.REPLY
        assert status.tape_colour == "White"
        assert status.text_colour == "Black"
        assert not status.has_errors

    def test_24mm_non_laminated(self):
        data = bytearray(32)
        data[10] = 24
        data[11] = 0x03  # non-laminated
        status = parse_status(bytes(data))

        assert status.media_width_mm == 24
        assert status.media_kind == MediaKind.NON_LAMINATED_TAPE

    def test_heat_shrink_tube(self):
        data = bytearray(32)
        data[11] = 0x11
        status = parse_status(bytes(data))
        assert status.media_kind == MediaKind.HEAT_SHRINK_TUBE

    def test_flexible_tape(self):
        data = bytearray(32)
        data[11] = 0x14
        status = parse_status(bytes(data))
        assert status.media_kind == MediaKind.FLEXIBLE_TAPE

    def test_unknown_media_kind(self):
        data = bytearray(32)
        data[11] = 0xAB  # unknown value
        status = parse_status(bytes(data))
        assert status.media_kind == MediaKind.INCOMPATIBLE_TAPE

    def test_wrong_length_raises(self):
        with pytest.raises(ValueError, match="Expected 32 bytes"):
            parse_status(b"\x00" * 10)

        with pytest.raises(ValueError, match="Expected 32 bytes"):
            parse_status(b"\x00" * 33)

    def test_empty_raises(self):
        with pytest.raises(ValueError):
            parse_status(b"")


class TestError1Flags:
    def test_no_media(self):
        data = bytearray(32)
        data[8] = 0x01
        status = parse_status(bytes(data))
        assert Error1.NO_MEDIA in status.error1
        assert status.has_errors
        assert "NO_MEDIA" in status.error_descriptions

    def test_cutter_jam(self):
        data = bytearray(32)
        data[8] = 0x04
        status = parse_status(bytes(data))
        assert Error1.CUTTER_JAM in status.error1
        assert status.has_errors

    def test_weak_battery(self):
        data = bytearray(32)
        data[8] = 0x08
        status = parse_status(bytes(data))
        assert Error1.WEAK_BATTERY in status.error1

    def test_high_voltage(self):
        data = bytearray(32)
        data[8] = 0x40
        status = parse_status(bytes(data))
        assert Error1.HIGH_VOLTAGE in status.error1

    def test_multiple_error1_flags(self):
        data = bytearray(32)
        data[8] = 0x01 | 0x08  # NO_MEDIA + WEAK_BATTERY
        status = parse_status(bytes(data))
        assert Error1.NO_MEDIA in status.error1
        assert Error1.WEAK_BATTERY in status.error1
        assert status.has_errors
        descs = status.error_descriptions
        assert "NO_MEDIA" in descs
        assert "WEAK_BATTERY" in descs


class TestError2Flags:
    def test_wrong_media(self):
        data = bytearray(32)
        data[9] = 0x01
        status = parse_status(bytes(data))
        assert Error2.WRONG_MEDIA in status.error2
        assert status.has_errors

    def test_cover_open(self):
        data = bytearray(32)
        data[9] = 0x10
        status = parse_status(bytes(data))
        assert Error2.COVER_OPEN in status.error2

    def test_overheat(self):
        data = bytearray(32)
        data[9] = 0x20
        status = parse_status(bytes(data))
        assert Error2.OVERHEAT in status.error2

    def test_combined_error1_and_error2(self):
        data = bytearray(32)
        data[8] = 0x01  # NO_MEDIA
        data[9] = 0x10  # COVER_OPEN
        status = parse_status(bytes(data))
        assert status.has_errors
        descs = status.error_descriptions
        assert "NO_MEDIA" in descs
        assert "COVER_OPEN" in descs


class TestStatusType:
    def test_reply(self):
        data = bytearray(32)
        data[18] = 0x00
        assert parse_status(bytes(data)).status_type == StatusType.REPLY

    def test_completed(self):
        data = bytearray(32)
        data[18] = 0x01
        assert parse_status(bytes(data)).status_type == StatusType.COMPLETED

    def test_error(self):
        data = bytearray(32)
        data[18] = 0x02
        assert parse_status(bytes(data)).status_type == StatusType.ERROR

    def test_turned_off(self):
        data = bytearray(32)
        data[18] = 0x04
        assert parse_status(bytes(data)).status_type == StatusType.TURNED_OFF

    def test_notification(self):
        data = bytearray(32)
        data[18] = 0x05
        assert parse_status(bytes(data)).status_type == StatusType.NOTIFICATION

    def test_phase_change(self):
        data = bytearray(32)
        data[18] = 0x06
        assert parse_status(bytes(data)).status_type == StatusType.PHASE_CHANGE

    def test_unknown_status_type(self):
        data = bytearray(32)
        data[18] = 0xFF
        assert parse_status(bytes(data)).status_type == StatusType.ERROR


class TestColours:
    def test_known_tape_colour(self):
        data = bytearray(32)
        data[24] = 0x04  # Red
        assert parse_status(bytes(data)).tape_colour == "Red"

    def test_known_text_colour(self):
        data = bytearray(32)
        data[25] = 0x08  # Black
        assert parse_status(bytes(data)).text_colour == "Black"

    def test_unknown_tape_colour(self):
        data = bytearray(32)
        data[24] = 0xAA
        status = parse_status(bytes(data))
        assert "Unknown" in status.tape_colour
        assert "0xAA" in status.tape_colour

    def test_unknown_text_colour(self):
        data = bytearray(32)
        data[25] = 0xBB
        status = parse_status(bytes(data))
        assert "Unknown" in status.text_colour


class TestStatusResponseProperties:
    def test_no_errors(self):
        status = parse_status(bytes(32))
        assert not status.has_errors
        assert status.error_descriptions == []

    def test_frozen_dataclass(self):
        status = parse_status(bytes(32))
        with pytest.raises(AttributeError):
            status.media_width_mm = 99  # type: ignore[misc]


class TestBuildPrintInfo:
    def test_correct_prefix(self):
        result = build_print_info(24, 100)
        assert result[:3] == b"\x1b\x69\x7a"  # ESC i z

    def test_total_length(self):
        result = build_print_info(12, 500)
        assert len(result) == 13

    def test_width_byte(self):
        result = build_print_info(12, 100)
        assert result[5] == 12

        result = build_print_info(24, 100)
        assert result[5] == 24

    def test_raster_count_le(self):
        result = build_print_info(24, 300)
        # Bytes 7-10 are the raster line count as LE uint32
        count = struct.unpack("<I", result[7:11])[0]
        assert count == 300

    def test_large_raster_count(self):
        result = build_print_info(24, 65535)
        count = struct.unpack("<I", result[7:11])[0]
        assert count == 65535


class TestBuildModeCommand:
    def test_auto_cut_on(self):
        result = build_mode_command(auto_cut=True)
        assert result[:3] == b"\x1b\x69\x4d"
        assert result[3] == 0x40  # auto-cut bit set

    def test_auto_cut_off(self):
        result = build_mode_command(auto_cut=False)
        assert result[:3] == b"\x1b\x69\x4d"
        assert result[3] == 0x00  # no auto-cut


class TestBuildPrintJob:
    def test_starts_with_init(self):
        job = build_print_job(b"Z", 1, 24)
        assert job[:64] == CMD_INITIALIZE

    def test_contains_reset(self):
        job = build_print_job(b"Z", 1, 24)
        assert CMD_RESET in job

    def test_ends_with_print_and_feed(self):
        job = build_print_job(b"Z", 1, 24)
        assert job[-1:] == CMD_PRINT_AND_FEED

    def test_raster_data_included(self):
        raster = b"G\x02\x00\xf1\x00"  # compressed blank line
        job = build_print_job(raster, 1, 24)
        assert raster in job

    def test_auto_cut_flag_in_mode(self):
        job_cut = build_print_job(b"Z", 1, 24, auto_cut=True)
        job_nocut = build_print_job(b"Z", 1, 24, auto_cut=False)
        # Mode command byte differs
        assert job_cut != job_nocut

    def test_compression_flag(self):
        job_tiff = build_print_job(b"Z", 1, 24, compression=True)
        job_none = build_print_job(b"Z", 1, 24, compression=False)
        # Compression command differs: M\x02 vs M\x00
        assert b"\x4d\x02" in job_tiff
        assert b"\x4d\x00" in job_none
