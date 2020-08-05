"""Video classes"""
import json
import logging
import subprocess
import wave

from galaxy.datatypes.binary import Binary
from galaxy.datatypes.metadata import MetadataElement
log = logging.getLogger(__name__)


def ffprobe(path):
    data = json.loads(subprocess.check_output(['ffprobe', '-loglevel', 'quiet', '-show_format', '-show_streams', '-of', 'json', path]))
    return data['format'], data['streams']

class Video(Binary):

    MetadataElement(name="resolution_w", default=0, desc="Width of video stream", readonly=True, visible=True, optional=True, no_value=0)
    MetadataElement(name="resolution_h", default=0, desc="Height of video stream", readonly=True, visible=True, optional=True, no_value=0)
    MetadataElement(name="fps", default=0, desc="FPS of video stream", readonly=True, visible=True, optional=True, no_value=0)
    MetadataElement(name="video_codecs", default="", desc="Video codec(s)", readonly=True, visible=True, optional=True, no_value="")
    MetadataElement(name="audio_codecs", default="", desc="Audio codec(s)", readonly=True, visible=True, optional=True, no_value="")
    MetadataElement(name="video_streams", default=0, desc="Number of video streams", readonly=True, visible=True, optional=True, no_value=0)
    MetadataElement(name="audio_streams", default=0, desc="Number of audio streams", readonly=True, visible=True, optional=True, no_value=0)

    def _get_resolution(self, streams):
        for stream in streams:
            if stream['codec_type'] == 'video':
                w = stream['width']
                h = stream['height']
                dividend, divisor = stream['avg_frame_rate'].split('/')
                fps = float(dividend) / float(divisor)
        else:
            w = h = fps = 0
        return w, h, fps

    def set_meta(self, dataset, **kwd):
        metadata, streams = ffprobe(dataset.file_name)
        (w, h, fps) = self._get_resolution(streams)
        dataset.metadata.resolution_w = w
        dataset.metadata.resolution_h = h
        dataset.metadata.fps = fps

        dataset.metadata.audio_codecs = '|'.join([stream['codec_name'] for stream in streams if stream['codec_type'] == 'audio'])
        dataset.metadata.video_codecs = '|'.join([stream['codec_name'] for stream in streams if stream['codec_type'] == 'video'])

        dataset.metadata.audio_streams = len([stream for stream in streams if stream['codec_type'] == 'audio'])
        dataset.metadata.video_streams = len([stream for stream in streams if stream['codec_type'] == 'video'])


class Mkv(Video):
    file_ext = "mkv"

    def sniff(self, filename):
        try:
            metadata, streams = ffprobe(filename)
            return 'matroska' in metadata['format_name'].split(',')
        except subprocess.CalledProcessException:
            return False


Binary.register_sniffable_binary_format("mkv", "mkv", Mkv)


class Mp4(Video):
    file_ext = "mp4"

    def sniff(self, filename):
        try:
            metadata, streams = ffprobe(filename)
            return 'mp4' in metadata['format_name'].split(',')
        except subprocess.CalledProcessException:
            return False


Binary.register_sniffable_binary_format("mp4", "mp4", Mp4)


class Flv(Video):
    file_ext = "flv"

    def sniff(self, filename):
        try:
            metadata, streams = ffprobe(filename)
            return 'flv' in metadata['format_name'].split(',')
        except subprocess.CalledProcessException:
            return False


Binary.register_sniffable_binary_format("flv", "flv", Flv)


class Mpg(Video):
    file_ext = "mpg"

    def sniff(self, filename):
        try:
            metadata, streams = ffprobe(filename)
            return 'mpegvideo' in metadata['format_name'].split(',')
        except subprocess.CalledProcessException:
            return False


Binary.register_sniffable_binary_format("mpg", "mpg", Mpg)


class WAV( Binary ):
    """RIFF WAV audio file"""

    file_ext = "wav"
    blurb = "RIFF WAV Audio file"
    is_binary = True

    MetadataElement( name="rate", desc="Sample Rate", default=0, no_value=0, readonly=True, visible=True, optional=True )
    MetadataElement( name="nframes", desc="Number of Samples", default=0, no_value=0, readonly=True, visible=True, optional=True )
    MetadataElement( name="nchannels", desc="Number of Channels", default=0, no_value=0, readonly=True, visible=True, optional=True )
    MetadataElement( name="sampwidth", desc="Sample Width", default=0, no_value=0, readonly=True, visible=True, optional=True )

    def get_mime(self):
        """Returns the mime type of the datatype"""
        return 'audio/wav'

    def sniff(self, filename):
        """
        >>> from galaxy.datatypes.sniff import get_test_fname
        >>> fname = get_test_fname('hello.wav')
        >>> WAV().sniff(fname)
        True

        >>> fname = get_test_fname('drugbank_drugs.cml')
        >>> WAV().sniff(fname)
        False
        """

        try:
            fp = wave.open(filename, 'rb')
            fp.close()
            return True
        except wave.Error:
            return False

    def set_meta(self, dataset, overwrite=True, **kwd):
        """Set the metadata for this dataset from the file contents
        """

        fd = wave.open(dataset.dataset.file_name, 'rb')
        dataset.metadata.rate = fd.getframerate()
        dataset.metadata.nframes = fd.getnframes()
        dataset.metadata.sampwidth = fd.getsampwidth()
        dataset.metadata.nchannels = fd.getnchannels()
        fd.close()


Binary.register_sniffable_binary_format('wav', 'wav', WAV)
