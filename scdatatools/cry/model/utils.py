from pyquaternion import Quaternion


def quaternion_to_dict(quat: Quaternion) -> dict[str, float]:
    """ Returns a diction representation of a :class:`Quaternion` """
    return {'x': quat.x, 'y': quat.y, 'z': quat.z, 'w': quat.w}


class Vector3D(dict):
    """ Convenience dictionary that inits with 'x,y,z' and has properties to access x,y,z """
    def __init__(self, x=0, y=0, z=0):
        super().__init__(x=float(x), y=float(y), z=float(z))

    def __setitem__(self, key, value):
        if key in ['x', 'y', 'z']:
            return dict.__setitem__(self, key, float(value))
        return dict.__setitem__(self, key, value)

    @property
    def x(self):
        return self['x']

    @x.setter
    def x(self, value):
        self['x'] = value

    @property
    def y(self):
        return self['y']

    @y.setter
    def y(self, value):
        self['y'] = value

    @property
    def z(self):
        return self['z']

    @z.setter
    def z(self, value):
        self['z'] = value
