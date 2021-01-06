import ctypes
from enum import IntEnum

HIRC_SIGNATURE = b'HIRC'


class HIRCSettingsTypes(IntEnum):
    voice_volume = 0
    voice_lowpass_filter = 3


class HIRCObjectTypes(IntEnum):
    settings = 1
    sfx = 2
    event_action = 3
    event = 4
    random = 5
    switch = 6
    actor_mixer = 7
    audio_bus = 8
    blend_container = 9
    music_segment = 10
    music_track = 11
    music_switch_container = 12
    music_playlist_container = 13
    attenuation = 14
    dialogue_event = 15
    motion_bus = 16
    motion_fx = 17
    effect = 18
    auxiliary_bus = 20


class HIRCUnknown(ctypes.LittleEndianStructure):
    _pack_ = 1
    _fields_ = [
        ("type", ctypes.c_byte),
        ("length", ctypes.c_uint32)
    ]


class HIRCSettings(ctypes.LittleEndianStructure):
    _pack_ = 1
    _fields_ = [
        ("type", ctypes.c_byte),
        ("length", ctypes.c_uint32),
        ("id", ctypes.c_uint32),
        ("num_settings", ctypes.c_byte),
    ]

    @classmethod
    def from_buffer(cls, source, offset=0):
        settings = type(cls).from_buffer(cls, source, offset)
        settings.settings = []

        offset += ctypes.sizeof(HIRCSettings)
        for i in range(settings.num_settings):
            settings.settings.append([HIRCSettingsTypes(source[offset + i])])

        offset += settings.num_settings
        for i in range(settings.num_settings):
            settings.settings[i].append(ctypes.c_float.from_buffer(source, offset + i))

        return settings


class AudioBusParameterType(IntEnum):
    voice_volume = 0
    voice_pitch = 2
    voice_lowpass_filter = 3
    bus_volums = 4


class HIRCAudioBus(ctypes.LittleEndianStructure):
    _pack_ = 1
    _fields_ = [
        ("type", ctypes.c_byte),
        ("length", ctypes.c_uint32),
        ("id", ctypes.c_uint32),
        ("parent_id", ctypes.c_uint32),
        ("num_additional_params", ctypes.c_byte),
    ]

    @classmethod
    def from_buffer(cls, source, offset=0):
        ab = type(cls).from_buffer(cls, source, offset)

        # TODO: flesh out audio bus params
        #   http://wiki.xentax.com/index.php/Wwise_SoundBank_(*.bnk)#type_.238:_Audio_Bus

        return ab


class HIRCMotionBus(ctypes.LittleEndianStructure):
    _pack_ = 1
    _fields_ = [
        ("type", ctypes.c_byte),
        ("length", ctypes.c_uint32),
    ]

    # TODO: structure is unknown


class HIRCMotionFX(ctypes.LittleEndianStructure):
    _pack_ = 1
    _fields_ = [
        ("type", ctypes.c_byte),
        ("length", ctypes.c_uint32),
    ]

    # TODO: structure is unknown


class HIRCEffect(ctypes.LittleEndianStructure):
    _pack_ = 1
    _fields_ = [
        ("type", ctypes.c_byte),
        ("length", ctypes.c_uint32),
    ]

    # TODO: structure is unknown


class HIRCHeader(ctypes.LittleEndianStructure):
    _fields_ = [
        ("signature", ctypes.c_char * 4),
        ("length", ctypes.c_uint32),
        ("num_objects", ctypes.c_uint32)
    ]

    @classmethod
    def from_buffer(cls, source, offset=0):
        hirc = type(cls).from_buffer(cls, source, offset)
        assert(hirc.signature == HIRC_SIGNATURE)
        hirc.objects = []

        offset += ctypes.sizeof(hirc)
        for i in range(hirc.num_objects):
            obj_type = source[offset]
            obj = HIRC_OBJ_HEADER_FOR_TYPE.get(obj_type, HIRCUnknown).from_buffer(source, offset)
            obj.offset = offset
            hirc.objects.append(obj)
            offset += obj.length + ctypes.sizeof(HIRCUnknown)
        return hirc


HIRC_OBJ_HEADER_FOR_TYPE = {
    HIRCObjectTypes.settings: HIRCSettings,
    HIRCObjectTypes.sfx: HIRCUnknown,
    HIRCObjectTypes.event_action: HIRCUnknown,
    HIRCObjectTypes.event: HIRCUnknown,
    HIRCObjectTypes.random: HIRCUnknown,
    HIRCObjectTypes.switch: HIRCUnknown,
    HIRCObjectTypes.actor_mixer: HIRCUnknown,
    HIRCObjectTypes.audio_bus: HIRCAudioBus,
    HIRCObjectTypes.blend_container: HIRCUnknown,
    HIRCObjectTypes.music_segment: HIRCUnknown,
    HIRCObjectTypes.music_track: HIRCUnknown,
    HIRCObjectTypes.music_switch_container: HIRCUnknown,
    HIRCObjectTypes.music_playlist_container: HIRCUnknown,
    HIRCObjectTypes.attenuation: HIRCUnknown,
    HIRCObjectTypes.dialogue_event: HIRCUnknown,
    HIRCObjectTypes.motion_bus: HIRCMotionBus,
    HIRCObjectTypes.motion_fx: HIRCMotionFX,
    HIRCObjectTypes.effect: HIRCEffect,
    HIRCObjectTypes.auxiliary_bus: HIRCUnknown,
}
