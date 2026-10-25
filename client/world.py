"""Low-poly arena and remote-player visuals."""
import math

from ursina import Entity, color, Vec3, time
from shared.constants import ARENA_LIMIT, COVER_BLOCKS

def build_arena():
    edge = ARENA_LIMIT + 2
    size = edge * 2
    Entity(model="plane", scale=size, texture="white_cube", texture_scale=(size, size), color=color.rgb32(48, 55, 50), collider="box")
    for pos, scale in [((0, 2, edge), (size, 4, 1)), ((0, 2, -edge), (size, 4, 1)), ((edge, 2, 0), (1, 4, size)), ((-edge, 2, 0), (1, 4, size))]:
        Entity(model="cube", position=pos, scale=scale, texture="white_cube", texture_scale=(size/8, 1), color=color.rgb32(62, 65, 63), collider="box")
    # Cover blocks
    for x, z, sx, sz in COVER_BLOCKS:
        Entity(model="cube", position=(x, 1.2, z), scale=(sx, 2.4, sz), texture="white_cube", texture_scale=(sx/2, sz/2), color=color.rgb32(72, 68, 61), collider="box")

class RemotePlayer(Entity):
    def __init__(self, data):
        super().__init__(model=None, collider=None)
        self.id = data["id"]
        self.username = data.get("username", "Jugador")
        self.paused = bool(data.get("paused", False))
        self.match_kills = data.get("match_kills", 0)
        self.match_deaths = data.get("match_deaths", 0)
        self.target_crouched = bool(data.get("crouched", False))
        self.target_position = Vec3(data.get("x", 0), data.get("y", 1) - 1, data.get("z", 0))
        self.target_yaw = data.get("yaw", 0)
        self.position = self.target_position
        self.rotation_y = self.target_yaw
        self.lower_cube = Entity(parent=self, model="cube", position=(0,.45,0), scale=(.8,.9,.8), color=color.rgb32(158,58,49), collider=None)
        self.upper_cube = Entity(parent=self, model="cube", position=(0,1.35,0), scale=(.8,.9,.8), color=color.rgb32(195,77,61), collider=None)
        self.upper_cube.enabled = not self.target_crouched
        from ursina import Text
        self.tag = Text(parent=self, text=self.username, y=2.0, scale=6, billboard=True, origin=(0,0), background=True)
        self.apply(data, snap=True)

    def apply(self, d, snap=False):
        self.target_crouched = bool(d.get("crouched", self.target_crouched))
        server_y = d.get("y", self.target_position.y + 1)
        self.target_position = Vec3(d.get("x", self.target_position.x), server_y - 1,
                                    d.get("z", self.target_position.z))
        self.target_yaw = d.get("yaw", self.target_yaw)
        self.match_kills = d.get("match_kills", self.match_kills)
        self.match_deaths = d.get("match_deaths", self.match_deaths)
        self.paused = bool(d.get("paused", self.paused))
        self.tag.text = f"{self.username} · PAUSA" if self.paused else self.username
        self.upper_cube.enabled = not self.target_crouched
        if snap:
            self.position = self.target_position
            self.rotation_y = self.target_yaw
            self.lower_cube.position = (0, .5 if self.target_crouched else .45, 0)
            self.lower_cube.scale = (.8, 1.0 if self.target_crouched else .9, .8)
            self.upper_cube.position = (0, 1.35, 0)
            self.upper_cube.scale = (.8,.9,.8)
        self.tag.y = 1.25 if self.target_crouched else 2.0
        self.enabled = d.get("alive", True)

    def update(self):
        # Network snapshots arrive at 20 Hz; interpolate at render rate, including jumps.
        blend = 1 - math.exp(-14 * min(max(time.dt, .001), .1))
        self.position += (self.target_position - self.position) * blend
        target_lower_y = .5 if self.target_crouched else .45
        target_lower_height = 1.0 if self.target_crouched else .9
        self.lower_cube.y += (target_lower_y - self.lower_cube.y) * blend
        self.lower_cube.scale_y += (target_lower_height - self.lower_cube.scale_y) * blend
        yaw_delta = (self.target_yaw - self.rotation_y + 180) % 360 - 180
        self.rotation_y = (self.rotation_y + yaw_delta * blend) % 360
