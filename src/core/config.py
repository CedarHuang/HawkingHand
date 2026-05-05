import json

from core import common
from core import logger
from core import startup
from core.callbacks import callbacks, CallbackEvent
from core.models import Event, Settings


class EventManager(list[Event]):
    def __init__(self):
        super().__init__()
        try:
            with open(common.event_config_path(), 'r', encoding='utf-8') as file:
                self.extend([Event.from_dict(i) for i in json.load(file)])
        except FileNotFoundError:
            logger.app.warning(f'Event configuration file not found at {common.event_config_path()}')
        except:
            logger.app.error(f'Failed to load event configuration from {common.event_config_path()}:', exc_info=True)

    def save(self):
        with open(common.event_config_path(), 'w', encoding='utf-8') as file:
            dicts = [event.to_dict() for event in self]
            json.dump(dicts, file, indent=4, ensure_ascii=False)
        callbacks.trigger(CallbackEvent.EVENTS_CHANGED)

    def insert(self, index, event):
        super().insert(index, event)
        self.save()

    def append(self, event):
        super().append(event)
        self.save()

    def pop(self, index):
        super().pop(index)
        self.save()

    def swap(self, a, b):
        self[a], self[b] = self[b], self[a]
        self.save()

    def move(self, fromIndex, toIndex):
        if fromIndex == toIndex:
            return
        item = super().pop(fromIndex)
        super().insert(toIndex, item)
        self.save()

    def update(self, index, event):
        if index < 0 or index >= len(self):
            return self.append(event)
        self[index] = event
        self.save()


events = EventManager()


class SettingsManager:
    def __init__(self):
        self._settings = Settings()
        try:
            with open(common.settings_config_path(), 'r', encoding='utf-8') as file:
                self._settings = Settings.from_dict(json.load(file))
        except FileNotFoundError:
            logger.app.warning(f'Settings configuration file not found at {common.settings_config_path()}')
        except:
            logger.app.error(f'Failed to load settings configuration from {common.settings_config_path()}:', exc_info=True)

    def __getattr__(self, name):
        if name.startswith('_'):
            raise AttributeError(name)
        return getattr(self._settings, name)

    def __setattr__(self, name, value):
        if name.startswith('_'):
            super().__setattr__(name, value)
        else:
            setattr(self._settings, name, value)

    def save(self, update_startup=False):
        with open(common.settings_config_path(), 'w', encoding='utf-8') as file:
            json.dump(self._settings.to_dict(), file, indent=4, ensure_ascii=False)
        callbacks.trigger(CallbackEvent.TRAY_UPDATE)
        if update_startup:
            startup.update_startup(self._settings.startup, self._settings.startup_as_admin)


settings = SettingsManager()
