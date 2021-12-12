from __future__ import annotations

import json
from json import JSONDecodeError
from pathlib import Path
from typing import Optional, Dict

import requests
from dataclasses import dataclass, field
from requests import RequestException, Response

from core.base.model.ProjectAliceObject import ProjectAliceObject
from core.util.model.Logger import Logger


class UnauthorizedUser(Exception):
	pass
class LinkButtonNotPressed(Exception):
	pass
class NoPhueIP(Exception):
	pass
class NoPhueBridgeFound(Exception):
	pass
class IPNotPhueBridge(Exception):
	pass
class PhueRegistrationError(Exception):
	pass
class PhueRequestError(Exception):
	pass
class SelectorError(Exception):
	pass
class NoSuchLight(Exception):
	pass
class NoSuchGroup(Exception):
	pass
class NoSuchScene(Exception):
	pass
class NoSuchSceneInGroup(Exception):
	pass
class NoSuchSceneInLight(Exception):
	pass
class LightNotReachable(Exception):
	pass


class Bridge(ProjectAliceObject):

	def __init__(self, ip: str = None, deviceName: str = 'phuepython', username: str = '', confFile: Path = Path('phueAPI.json')):
		super().__init__(prepend='[Phue API]')
		self._ip = ip
		self._deviceName = deviceName
		self._confFile = confFile
		self._username = username
		self._connected = False

		self._groups: Dict[int, Group] = dict()
		self._lights: Dict[int, Light] = dict()
		self._scenes: Dict[str, Scene] = dict()

		conf = self.loadConfigFileData()
		if conf:
			if ip and ip != conf['ip']:
				self._confFile.unlink()
			elif not ip:
				self._ip = conf['ip']

			if username and username != conf['ip']:
				self._confFile.unlink()
			elif not username:
				self._username = conf['username']


	@property
	def lights(self) -> dict:
		return self._lights


	def light(self, lightId: int = 0, lightName: str = '') -> Light:
		if lightId == 0 and not lightName:
			raise SelectorError('Cannot get light without id and/or name')

		if lightId == 0:
			for light in self._lights.values():
				if light.name == lightName:
					return light
			raise NoSuchLight
		else:
			if lightId not in self._lights:
				raise NoSuchLight
			return self._lights[lightId]


	@property
	def groups(self) -> dict:
		return self._groups


	@property
	def groupsByName(self) -> dict:
		return {group.name: group for group in self._groups.values()}


	def group(self, groupId: int = 0, groupName: str = '') -> Group:
		if groupName:
			for group in self._groups.values():
				if group.name == groupName:
					return group
			raise NoSuchGroup
		else:
			if groupId not in self._groups:
				raise NoSuchGroup
			return self._groups[groupId]


	@property
	def scenes(self) -> dict:
		return self._scenes


	@property
	def scenesByName(self) -> dict:
		return {scene.name: scene for name, scene in self._scenes.items()}


	def scene(self, sceneId: str = '', sceneName: str = '') -> Scene:
		if not sceneId and not sceneName:
			raise SelectorError('Cannot get scene without id and/or name')

		if not sceneId:
			for scene in self._scenes.values():
				if scene.name == sceneName:
					return scene
			raise NoSuchScene
		else:
			if sceneId not in self._scenes:
				raise NoSuchScene
			return self._scenes[sceneId]


	def addGroup(self, groupName: str, lights: list, groupType: str = 'LightGroup', clazz: str = 'Other'):
		groupType = 'LightGroup' if groupType not in ('LightGroup', 'Room', 'Luminaire', 'LightSource', 'Zone', 'Entertainment') else groupType
		body = {
			'lights': lights,
			'name': groupName,
			'type': groupType
		}

		if groupType == 'Room':
			body['class'] = clazz

		self.sendAuthRequest(url='/groups', method='POST', data=body)


	@property
	def connected(self) -> bool:
		return self._connected


	def loadConfigFileData(self) -> Optional[dict]:
		try:
			if not self._confFile.exists():
				return None

			with self._confFile.open() as fp:
				return json.load(fp)
		except Exception as e:
			self.logError(f'Error opening config file: {e}')
			return None


	def saveConfigFile(self):
		try:
			self._confFile.write_text(json.dumps({'ip': self._ip, 'username': self._username}))
		except Exception as e:
			self.logError(f'Error saving config file: {e}')
			return None


	def connect(self, autodiscover: bool = True) -> bool:
		try:
			if not self._ip and autodiscover:
				self.autodiscover()
			elif not self._ip and not autodiscover:
				raise NoPhueIP

			if not self._username:
				raise UnauthorizedUser

			req = self.sendRequest(url=f'/{self._username}')
			if not req:
				raise OSError

			answer = req.json()
			if self.errorReturned(answer):
				raise UnauthorizedUser

			self._connected = True
		except OSError as e:
			self.logError(f'Bridge connection error: {e}')
			return False
		except (UnauthorizedUser, NoPhueIP, NoPhueBridgeFound):
			raise
		except Exception as e:
			self.logError(f'Something went wrong connecting to the bridge: {e}')
			return False

		try:
			self.loadDevices()
		except Exception as e:
			self.logError(f'Something went wrong loading devices assigned to the bridge: {e}')

		return True


	def register(self, saveConnection: bool = True) -> bool:
		try:
			req = self.sendRequest(data={'devicetype': f'phueAPI#{self._deviceName}'}, method='POST')
			if not req:
				raise PhueRegistrationError

			answer = req.json()
			if self.errorReturned(answer):
				raise LinkButtonNotPressed
			elif self.successReturned(answer):
				if saveConnection:
					self._username = answer[0]['success']['username']
					self.saveConfigFile()
				return True
			else:
				raise Exception('Unsupported answer from bridge while registering')
		except LinkButtonNotPressed:
			raise
		except Exception as e:
			raise PhueRegistrationError(f'Bridge register failed: {e}')


	@staticmethod
	def isPhueBridge(ip) -> bool:
		if not ip:
			return False
		try:
			# noinspection HttpUrlsUsage
			req = requests.get(f'http://{ip}/api/config', timeout=2)
			data = req.json()
			if 'swversion' in data and 'bridgeid' in data:
				return True

			return False
		except Exception:
			return False


	def sendAuthRequest(self, url: str, data: dict = None, method: str = 'GET', silent: bool = False) -> Optional[Response]:
		if self._username not in url:
			url = f'/{self._username}{"/" if not url.startswith("/") else ""}{url}'
		return self.sendRequest(url=url, data=data, method=method, silent=silent)


	def sendRequest(self, url: str = None, data: dict = None, method: str = 'GET', silent: bool = False) -> Optional[Response]:
		data = data or dict()
		url = url or '/api'
		if not url.startswith('/api'):
			url = f'/api{"/" if not url.startswith("/") else ""}{url}'

		try:
			# noinspection HttpUrlsUsage
			req = requests.request(method=method, url=f'http://{self._ip}{url}', data=json.dumps(data), timeout=2)
			return req
		except Exception as e:
			if not silent:
				raise PhueRequestError(f'API request error: {e}')
			return None


	def autodiscover(self):
		self.logInfo('Trying to autodiscover the bridge on the network')
		try:
			request = requests.get('https://discovery.meethue.com/')
			self.logInfo('Obtained a list of potential devices')
			for device in request.json():
				self.logInfo(f'Testing {device["internalipaddress"]}')
				if self.isPhueBridge(device['internalipaddress']):
					self._ip = device['internalipaddress']
					self.saveConfigFile()
					self.logInfo(f'Found bridge at {self._ip}')
					return
			raise NoPhueBridgeFound
		except (RequestException, JSONDecodeError):
			self.logError('Something went wrong trying to discover the bridge on your network')


	def loadDevices(self):
		# First add group 0, which is a special group containing all the lights
		group = Group()
		group.init(0, self)
		group.name = 'everywhere'
		group.state = {'all_on': False, 'any_on': False}
		self._groups[0] = group

		req = self.sendAuthRequest(url='/groups')
		if req:
			answer = req.json()
			for groupId, data in answer.items():
				if 'class' in data:
					data['clazz'] = data.pop('class')
				groupId = int(groupId)
				group = Group(**data)
				group.init(groupId, self)
				self._groups[groupId] = group

		req = self.sendAuthRequest(url='/lights')
		if req:
			answer = req.json()
			for lightId, data in answer.items():
				lightId = int(lightId)
				light = Light(**data)
				light.init(lightId, self)
				self._lights[lightId] = light

		if req:
			req = self.sendAuthRequest(url='/scenes')
			answer = req.json()
			for sceneId, data in answer.items():
				scene = Scene(**data)
				scene.init(sceneId, self)
				self._scenes[sceneId] = scene

				if not 'type' in data:
					continue

				if data['type'] == 'GroupScene':
					try:
						self.group(int(data['group'])).myScenes.append(sceneId)
					except NoSuchGroup:
						pass
				elif data['type'] == 'LightScene' and data['name'] != 'Last on state':
					for lightId in data['lights']:
						try:
							self.light(int(lightId)).myScenes.append(sceneId)
						except NoSuchLight:
							pass


	@staticmethod
	def errorReturned(answer: dict) -> bool:
		return isinstance(answer, list) and 'error' in answer[0]


	@staticmethod
	def successReturned(answer: dict) -> bool:
		return isinstance(answer, list) and 'success' in answer[0]


@dataclass
class Light(object):
	state: dict
	swupdate: dict
	type: str
	name: str
	modelid: str
	manufacturername: str
	productname: str
	capabilities: dict
	config: dict
	uniqueid: str
	swversion: str
	swconfigid: str = ''
	productid: str = ''
	id: int = 0
	bridge: Optional[Bridge] = None
	myScenes: list = field(default_factory=list)
	logger: Logger = Logger(prepend='[Phue Light]')


	def init(self, lightId: int, bridgeInstance: Bridge):
		self.id = lightId
		self.bridge = bridgeInstance
		self.name = self.name.lower()


	def __str__(self) -> str:
		return f'Light id {self.id} named "{self.name}" of type {self.type}.'


	def on(self): #NOSONAR
		self.request(url=f'/{self.id}/state', method='PUT', data={'on': True})


	def off(self):
		self.request(url=f'/{self.id}/state', method='PUT', data={'on': False})


	@property
	def isOn(self) -> bool:
		return self.state['on']


	@property
	def isOff(self) -> bool:
		return not self.state['on']


	def alert(self, state: str = 'lselect'):
		self.request(url=f'/{self.id}/state', method='PUT', data={'alert': state})


	def effect(self, effect: str = 'colorloop'):
		self.request(url=f'/{self.id}/state', method='PUT', data={'effect': effect})


	def configure(self, data: dict, sendToBridge: bool = True):
		for key, value in data.items():
			if not key in self.state:
				continue

			self.state[key] = value

		if sendToBridge:
			self.request(url=f'/{self.id}/state', method='PUT', data=data)


	@property
	def brightness(self) -> int:
		return self.state['bri']


	# noinspection DuplicatedCode
	@brightness.setter
	def brightness(self, value: int):
		if value == 0:
			self.off()
			self.state['bri'] = 0
			return

		value = sorted((1, value, 254))[1]

		self.state['bri'] = value
		self.request(url=f'/{self.id}/state', method='PUT', data={'bri': value})


	@property
	def saturation(self) -> int:
		return self.state['sat']


	@saturation.setter
	def saturation(self, value: int):
		value = sorted((1, value, 254))[1]

		self.state['sat'] = value
		self.request(url=f'/{self.id}/state', method='PUT', data={'sat': value})


	@property
	def hue(self) -> int:
		return self.state['hue']


	@hue.setter
	def hue(self, value: int):
		value = sorted((0, value, 65535))[1]

		self.state['hue'] = value
		self.request(url=f'/{self.id}/state', method='PUT', data={'hue': value})


	@property
	def xy(self) -> list: #NOSONAR
		return self.state['xy']


	@xy.setter
	def xy(self, value: list): #NOSONAR
		x = sorted((0, value[0], 1))[1] #NOSONAR
		y = sorted((0, value[1], 1))[1] #NOSONAR

		self.state['xy'] = [x, y]
		self.request(url=f'/{self.id}/state', method='PUT', data={'xy': value})


	@property
	def mired(self) -> int:
		return self.state['ct']


	@mired.setter
	def mired(self, value: int):
		self.state['ct'] = value
		self.request(url=f'/{self.id}/state', method='PUT', data={'ct': value})


	@property
	def colormode(self) -> str:
		return self.state.get('colormode', None)


	@colormode.setter
	def colormode(self, mode: str):
		if 'colormode' not in self.state:
			self.logger.logWarning(f'Light {self.name} with id {self.id} does not support colormode changing')
			return

		if mode not in ('hs', 'xy', 'ct'):
			mode = 'ct'
			self.logger.logWarning('Invalid color mode specified. Allowed value are "hs", "ct", "xy"')

		self.state['colormode'] = mode
		self.request(url=f'/{self.id}/state', method='PUT', data={'colormode': mode})


	@property
	def reachable(self) -> bool:
		return self.state['reachable']


	def delete(self):
		self.request(url=f'/{self.id}', method='DELETE')


	def request(self, url: str, data: dict = None, method: str = 'GET'):
		if not self.reachable or not self.bridge:
			raise LightNotReachable

		self.bridge.sendAuthRequest(url=f'/lights{"/" if not url.startswith("/") else ""}{url}', method=method, data=data)


@dataclass
class Group(object):
	name: str = ''
	lights: list = field(default_factory=list)
	sensors: list = field(default_factory=list)
	type: str = ''
	state: dict = field(default_factory=dict)
	recycle: bool = False
	action: dict = field(default_factory=dict)
	clazz: str = ''
	stream: dict = field(default_factory=dict)
	locations: dict = field(default_factory=dict)
	id: int = 0
	bridge: Optional[Bridge] = None
	myScenes: list = field(default_factory=list)


	def __str__(self) -> str:
		return f'Group id {self.id} named "{self.name}" with {len(self.lights)} lights and {len(self.sensors)} sensors attributed'


	def init(self, groupId: int, bridgeInstance: Bridge):
		self.id = groupId
		self.bridge = bridgeInstance
		self.name = self.name.lower()


	def on(self): #NOSONAR
		self.state['any_on'] = True
		self.state['all_on'] = True
		self.request(url=f'/{self.id}/action', method='PUT', data={'on': True})


	def off(self):
		self.state['any_on'] = False
		self.state['all_on'] = False
		self.request(url=f'/{self.id}/action', method='PUT', data={'on': False})


	@property
	def isOn(self) -> bool:
		return self.state['any_on']


	@property
	def isOff(self) -> bool:
		return not self.state['any_on']


	def toggle(self):
		if self.isOff:
			self.on()
		else:
			self.off()


	def alert(self, state: str = 'lselect'):
		self.request(url=f'/{self.id}/action', method='PUT', data={'alert': state})


	def effect(self, effect: str = 'colorloop'):
		self.request(url=f'/{self.id}/action', method='PUT', data={'effect': effect})


	def delete(self):
		self.request(url=f'/{self.id}', method='DELETE')


	@property
	def brightness(self) -> int:
		return self.action['bri']


	# noinspection DuplicatedCode
	@brightness.setter
	def brightness(self, value: int):
		if value == 0:
			self.off()
			self.action['bri'] = 0
			return

		value = sorted((1, value, 254))[1]

		self.action['bri'] = value
		self.request(url=f'/{self.id}/action', method='PUT', data={'bri': value})


	@property
	def saturation(self) -> int:
		return self.action['sat']


	@saturation.setter
	def saturation(self, value: int):
		value = sorted((1, value, 254))[1]

		self.action['sat'] = value
		self.request(url=f'/{self.id}/action', method='PUT', data={'sat': value})


	@property
	def hue(self) -> int:
		return self.action['hue']


	@hue.setter
	def hue(self, value: int):
		value = sorted((0, value, 65535))[1]

		self.action['hue'] = value
		self.request(url=f'/{self.id}/action', method='PUT', data={'hue': value})


	def scene(self, sceneId: str = '', sceneName: str = ''):
		if not sceneId and not sceneName:
			raise SelectorError('Cannot get scene without id and/or name')

		if not self.bridge:
			raise SelectorError('No bridge defined')


		if not sceneId:
			for sceneId in self.myScenes:
				if self.bridge.scenes[sceneId].name.lower() != sceneName.lower():
					continue

				self.request(url=f'/{self.id}/action', method='PUT', data={'scene': sceneId})
				return

			raise NoSuchSceneInGroup
		else:
			if sceneId not in self.bridge.scenes:
				raise NoSuchScene

			if sceneId not in self.myScenes:
				raise NoSuchSceneInGroup

			self.request(url=f'/{self.id}/action', method='PUT', data={'scene': sceneId})


	def rename(self, newName: str, allowExistingName: bool = False) -> bool:
		response = self.request(url=f'/{self.id}', method='PUT', data={'name': newName})

		if not response:
			return False

		for answer in response.json():
			if not 'success' in answer or not f'/groups/{self.id}/name' in answer['success']:
				continue

			if answer['success'][f'/groups/{self.id}/name'] == newName:
				self.name = newName
				return True

			elif answer['success'][f'/groups/{self.id}/name'] != newName and allowExistingName:
				self.name = answer['success'][f'/groups/{self.id}/name']
				return True

			elif not allowExistingName:
				self.request(url=f'/{self.id}', method='PUT', data={'name': self.name})
				return False

		return False


	def request(self, url: str, data: dict = None, method: str = 'GET') -> Optional[Response]:
		if not self.bridge:
			return None

		return self.bridge.sendAuthRequest(url=f'/groups{"/" if not url.startswith("/") else ""}{url}', method=method, data=data)


@dataclass
class Scene(object):
	name: str
	type: str
	lights: list
	owner: str
	recycle: bool
	locked: bool
	appdata: dict
	picture: str
	lastupdated: str
	version: int
	image: str = ''
	group: Optional[str] = ''
	id: Optional[str] = ''
	bridge: Optional[Bridge] = None


	def init(self, sceneId: str, bridgeInstance: Bridge):
		self.id = sceneId
		self.bridge = bridgeInstance
		self.name = self.name.lower()


	def __str__(self) -> str:
		return f'Scene id {self.id} named "{self.name}" with {len(self.lights)} lights'
