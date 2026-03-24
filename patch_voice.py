"""Monkey-patch discord.ext.voice_recv to handle RTCP and Opus decode errors gracefully."""

import logging
import struct

log = logging.getLogger("voice_patch")


def apply():
    try:
        # Patch 1: Fix the packet router to skip corrupted packets
        from discord.ext.voice_recv import router as vr_router

        _original_do_run = vr_router.PacketRouter._do_run

        def _safe_do_run(self):
            while not self._end_thread.is_set():
                self.waiter.wait()
                with self._lock:
                    for decoder in self.waiter.items:
                        try:
                            data = decoder.pop_data()
                            if data is not None:
                                self.sink.write(data.source, data)
                        except Exception:
                            pass  # Skip corrupted packets silently

        vr_router.PacketRouter._do_run = _safe_do_run
        log.info("Patch 1/3: PacketRouter error handling applied")

        # Patch 2: Filter RTCP packets before they reach the decoder
        from discord.ext.voice_recv import reader as vr_reader

        if hasattr(vr_reader, 'VoiceRecvClient'):
            _orig_process = getattr(vr_reader.VoiceRecvClient, '_process_raw_data', None)
            if _orig_process:
                def _filtered_process(self, data, addr):
                    if len(data) >= 2:
                        # Check for RTCP packet (version=2, payload type 200-204)
                        first_byte = data[0]
                        version = (first_byte >> 6) & 0x3
                        if version == 2 and len(data) >= 8:
                            pt = data[1] & 0x7F
                            if 200 <= pt <= 204:
                                return  # Silently drop RTCP packets
                    return _orig_process(self, data, addr)
                vr_reader.VoiceRecvClient._process_raw_data = _filtered_process
                log.info("Patch 2/3: RTCP packet filter applied")

        # Patch 3: Make opus decoder resilient to corrupted data
        from discord.ext.voice_recv import opus as vr_opus

        if hasattr(vr_opus, 'SsrcAudioDecoder'):
            _orig_decode = vr_opus.SsrcAudioDecoder._decode_packet

            def _safe_decode(self, packet):
                try:
                    return _orig_decode(self, packet)
                except Exception as e:
                    # On decode error, return silence instead of crashing
                    from discord.opus import Decoder
                    silence = b'\x00' * (Decoder.FRAME_SIZE * Decoder.SAMPLE_SIZE * Decoder.CHANNELS)
                    return packet, silence

            vr_opus.SsrcAudioDecoder._decode_packet = _safe_decode
            log.info("Patch 3/3: Opus decoder resilience applied")

        log.info("All voice patches applied successfully")

    except Exception as e:
        log.warning(f"Failed to apply voice patches: {e}")
