import ctypes
import struct
from enum import IntEnum
from scdatatools.utils import StructureWithEnums

HIRC_SIGNATURE = b'HIRC'


class HIRCSettingsTypes(IntEnum):
    voice_volume = 0
    voice_lowpass_filter = 3


class AudioBusParameterType(IntEnum):
    voice_volume = 0
    voice_pitch = 2
    voice_lowpass_filter = 3
    bus_volums = 4


class HIRCObjectTypes(IntEnum):
    settings = 1
    sound = 2
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
    unknown1 = 19
    auxiliary_bus = 20
    unknown2 = 21
    unknown3 = 22


class HIRCObject(ctypes.LittleEndianStructure, StructureWithEnums):
    _pack_ = 1
    _fields_ = [
        ("type", ctypes.c_byte),
        ("length", ctypes.c_uint32),
        ("id", ctypes.c_uint32),
    ]
    _map = {
        "type": HIRCObjectTypes
    }

    def __repr__(self):
        return f'<{self.__class__.__name__} type:{self.type.name} len:{self.length} id:{self.id}>'


class HIRCUnknown(HIRCObject):
    pass


class HIRCSettings(HIRCObject):
    # _pack_ = 1
    _fields_ = [
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


class HIRCSound(HIRCObject):
    _fields_ = [
        ("unknown", ctypes.c_uint32),
        ("method", ctypes.c_byte),
        ("wem_id", ctypes.c_uint32),
        ("source_id", ctypes.c_uint32),
        ("source_offset", ctypes.c_uint32),
    ]


class HIRCEventActionType(IntEnum):
    Stop = 0x01,
    Pause = 0x02,
    Resume = 0x03,
    Play = 0x04,
    Trigger = 0x05,
    Mute = 0x06,
    UnMute = 0x07,
    SetVoicePitch = 0x08,
    ResetVoicePitch = 0x09,
    SetVoiceVolume = 0x0A,
    ResetVoiceVolume = 0x0B,
    SetBusVolume = 0x0C,
    ResetBusVolume = 0x0D,
    SetVoiceLowpassFilter = 0x0E,
    ResetVoiceLowpassFilter = 0x0F,
    EnableState = 0x10,
    DisableState = 0x11,
    SetState = 0x12,
    SetGameParameter = 0x13,
    ResetGameParameter = 0x14,
    SetSwitch = 0x19,
    EnableBypassOrDisableBypass = 0x1A,
    ResetBypassEffect = 0x1B,
    Break = 0x1C,
    Seek = 0x1E,


class HIRCEventAction(HIRCObject):
    _fields_ = [
        ("scope", ctypes.c_byte),
        ("action_type", ctypes.c_byte),
        ("sound_id", ctypes.c_uint32),
        ("reserved", ctypes.c_byte),
        ("num_params", ctypes.c_byte),
    ]
    _map = {
        "type": HIRCObjectTypes,
        "action_type": HIRCEventActionType
    }


class HIRCEvent(HIRCObject):
    _fields_ = [
        ("num_actions", ctypes.c_byte),
    ]

    @classmethod
    def from_buffer(cls, source, offset=0):
        he = type(cls).from_buffer(cls, source, offset)
        he.event_actions = []

        offset += ctypes.sizeof(he)
        for i in range(he.num_actions):
            he.event_actions.append(struct.unpack_from('<I', source, offset)[0])
            offset += 4
        return he


class HIRCAudioBus(HIRCObject):
    # _pack_ = 1
    _fields_ = [
        ("parent_id", ctypes.c_uint32),
        ("num_additional_params", ctypes.c_byte),
    ]

    @classmethod
    def from_buffer(cls, source, offset=0):
        ab = type(cls).from_buffer(cls, source, offset)

        # TODO: flesh out audio bus params
        #   http://wiki.xentax.com/index.php/Wwise_SoundBank_(*.bnk)#type_.238:_Audio_Bus

        return ab


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

        for t in HIRCObjectTypes:
            setattr(hirc, t.name, {})

        offset += ctypes.sizeof(hirc)
        for i in range(hirc.num_objects):
            obj_type = source[offset]
            obj = HIRC_OBJ_HEADER_FOR_TYPE.get(obj_type, HIRCUnknown).from_buffer(source, offset)
            obj.offset = offset
            hirc.objects.append(obj)
            getattr(hirc, obj.type.name)[obj.id] = obj
            # the `id` is included in the length? so we're only adding the type/len size (5)
            offset += obj.length + 5
        return hirc


HIRC_OBJ_HEADER_FOR_TYPE = {
    HIRCObjectTypes.settings: HIRCSettings,
    HIRCObjectTypes.sound: HIRCSound,
    HIRCObjectTypes.event_action: HIRCEventAction,
    HIRCObjectTypes.event: HIRCEvent,
    HIRCObjectTypes.random: HIRCObject,
    HIRCObjectTypes.switch: HIRCObject,
    HIRCObjectTypes.actor_mixer: HIRCObject,
    HIRCObjectTypes.audio_bus: HIRCAudioBus,
    HIRCObjectTypes.blend_container: HIRCObject,
    HIRCObjectTypes.music_segment: HIRCObject,
    HIRCObjectTypes.music_track: HIRCObject,
    HIRCObjectTypes.music_switch_container: HIRCObject,
    HIRCObjectTypes.music_playlist_container: HIRCObject,
    HIRCObjectTypes.attenuation: HIRCObject,
    HIRCObjectTypes.dialogue_event: HIRCObject,
    HIRCObjectTypes.motion_bus: HIRCObject,
    HIRCObjectTypes.motion_fx: HIRCObject,
    HIRCObjectTypes.effect: HIRCObject,
    HIRCObjectTypes.auxiliary_bus: HIRCObject,
    HIRCObjectTypes.unknown1: HIRCObject,
    HIRCObjectTypes.unknown2: HIRCObject,
    HIRCObjectTypes.unknown3: HIRCObject,
}
