"""Suppress the noisy RTCP packet warnings from voice_recv."""
import logging

class RTCPFilter(logging.Filter):
    def filter(self, record):
        return "unexpected rtcp packet" not in record.getMessage().lower()

def apply():
    for name in ['discord.ext.voice_recv.reader', 'discord.ext.voice_recv']:
        logger = logging.getLogger(name)
        logger.addFilter(RTCPFilter())
