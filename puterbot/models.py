import io

from loguru import logger
from pynput import keyboard
from PIL import Image, ImageChops
import numpy as np
import sqlalchemy as sa

from puterbot.db import Base, Session
from puterbot.utils import take_screenshot


class Recording(Base):
    __tablename__ = "recording"

    id = sa.Column(sa.Integer, primary_key=True)
    timestamp = sa.Column(sa.Integer)
    monitor_width = sa.Column(sa.Integer)
    monitor_height = sa.Column(sa.Integer)
    double_click_interval_seconds = sa.Column(sa.Numeric(asdecimal=False))
    double_click_distance_pixels = sa.Column(sa.Numeric(asdecimal=False))
    platform = sa.Column(sa.String)
    task_description = sa.Column(sa.String)

    input_events = sa.orm.relationship("InputEvent", back_populates="recording")


class InputEvent(Base):
    __tablename__ = "input_event"

    id = sa.Column(sa.Integer, primary_key=True)
    name = sa.Column(sa.String)
    timestamp = sa.Column(sa.Integer)
    recording_timestamp = sa.Column(sa.ForeignKey("recording.timestamp"))
    screenshot_timestamp = sa.Column(sa.ForeignKey("screenshot.timestamp"))
    window_event_timestamp = sa.Column(sa.ForeignKey("window_event.timestamp"))
    mouse_x = sa.Column(sa.Numeric(asdecimal=False))
    mouse_y = sa.Column(sa.Numeric(asdecimal=False))
    mouse_dx = sa.Column(sa.Numeric(asdecimal=False))
    mouse_dy = sa.Column(sa.Numeric(asdecimal=False))
    mouse_button_name = sa.Column(sa.String)
    mouse_pressed = sa.Column(sa.Boolean)
    key_name = sa.Column(sa.String)
    key_char = sa.Column(sa.String)
    key_vk = sa.Column(sa.String)
    canonical_key_name = sa.Column(sa.String)
    canonical_key_char = sa.Column(sa.String)
    canonical_key_vk = sa.Column(sa.String)
    parent_id = sa.Column(sa.Integer, sa.ForeignKey("input_event.id"))

    children = sa.orm.relationship("InputEvent")
    recording = sa.orm.relationship("Recording", back_populates="input_events")
    screenshot = sa.orm.relationship("Screenshot")
    window_event = sa.orm.relationship("WindowEvent")

    # TODO: playback_timestamp / original_timestamp

    def _key(self, key_name, key_char, key_vk):
        if key_name:
            key = keyboard.Key[key_name]
        elif key_char:
            key = key_char
        elif key_vk:
            # TODO: verify this is correct
            key = keyboard.KeyCode.from_vk(int(key_vk))
        else:
            key = None
        return key

    @property
    def key(self):
        logger.debug(
            f"{self.name=} {self.key_name=} {self.key_char=} {self.key_vk=}"
        )
        return self._key(
            self.key_name,
            self.key_char,
            self.key_vk,
        )

    @property
    def canonical_key(self):
        logger.debug(
            f"{self.name=} "
            f"{self.canonical_key_name=} "
            f"{self.canonical_key_char=} "
            f"{self.canonical_key_vk=}"
        )
        return self._key(
            self.canonical_key_name,
            self.canonical_key_char,
            self.canonical_key_vk,
        )

    def _text(self, sep="-", name_prefix="<", name_suffix=">", canonical=False):
        if canonical:
            key_attr = self.canonical_key
            key_name_attr = self.canonical_key_name
        else:
            key_attr = self.key
            key_name_attr = self.key_name
        if self.children:
            parts = [
                child._text(canonical=canonical)
                for child in self.children
                if child.name == "press"
            ]
            if any(parts):
                # str is necessary for canonical=True named keys
                # e.g. canonical(<esc>) == <53> (darwin)
                text = sep.join([str(part) for part in parts])
            else:
                text = None
        else:
            if key_name_attr:
                text = f"{name_prefix}{key_attr}{name_suffix}".replace(
                    "Key.", "",
                )
            else:
                text = key_attr
        return text

    @property
    def text(self):
        return self._text()

    @property
    def canonical_text(self):
        return self._text(canonical=True)

    def __str__(self):
        attr_names = [
            "name",
            "mouse_x",
            "mouse_y",
            "mouse_dx",
            "mouse_dy",
            "mouse_button_name",
            "mouse_pressed",
            "key",
        ]
        attrs = [
            getattr(self, attr_name)
            for attr_name in attr_names
        ]
        attrs = [
            int(attr)
            if isinstance(attr, float)
            else attr
            for attr in attrs
        ]
        attrs = [str(attr) for attr in attrs if attr]
        rval = " ".join(attrs)
        return rval


class Screenshot(Base):
    __tablename__ = "screenshot"

    id = sa.Column(sa.Integer, primary_key=True)
    recording_timestamp = sa.Column(sa.Integer)
    timestamp = sa.Column(sa.Integer)
    png_data = sa.Column(sa.LargeBinary)
    # TODO: replace prev with prev_timestamp?

    # TODO: convert to png_data on save
    sct_img = None

    prev = None
    _image = None
    _diff = None
    _diff_mask = None

    @property
    def image(self):
        if not self._image:
            if self.sct_img:
                self._image = Image.frombytes(
                    "RGB",
                    self.sct_img.size,
                    self.sct_img.bgra,
                    "raw",
                    "BGRX",
                )
            else:
                buffer = io.BytesIO(self.png_data)
                self._image = Image.open(buffer)
        return self._image

    @property
    def diff(self):
        if not self._diff:
            assert self.prev, "Attempted to compute diff before setting prev"
            self._diff = ImageChops.difference(self.image, self.prev.image)
        return self._diff

    @property
    def diff_mask(self):
        if not self._diff_mask:
            self._diff_mask = self._diff.convert("1")
        return self._diff_mask

    @property
    def array(self):
        return np.array(self.image)

    @classmethod
    def take_screenshot(cls):
        sct_img = take_screenshot()
        screenshot = Screenshot(sct_img=sct_img)
        return screenshot

    @classmethod
    def get_active_window(cls):
        try:
            latest_window_event = Session.query(WindowEvent).order_by(
                sa.desc(WindowEvent.timestamp)).first()
            return latest_window_event
        finally:
            Session.close()

    def crop_active_window(self, window_event):
        #crops the screenshot's image to the bounds of the window_event
        if window_event:
            box = (
                window_event.left,
                window_event.top,
                window_event.left + window_event.width,
                window_event.top + window_event.height
            )
            self._image = self._image.crop(box)


class WindowEvent(Base):
    __tablename__ = "window_event"

    id = sa.Column(sa.Integer, primary_key=True)
    recording_timestamp = sa.Column(sa.Integer)
    timestamp = sa.Column(sa.Integer)
    title = sa.Column(sa.String)
    left = sa.Column(sa.Integer)
    top = sa.Column(sa.Integer)
    width = sa.Column(sa.Integer)
    height = sa.Column(sa.Integer)
