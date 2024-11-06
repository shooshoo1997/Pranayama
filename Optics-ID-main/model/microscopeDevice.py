from typing import NamedTuple
from hardwarelibrary.notificationcenter import NotificationCenter as notif
from tools.CircularList import RingBuffer
import numpy as np
from threading import Lock
# from hardwarelibrary.motion.sutterdevice import Direction


class DataPoint(NamedTuple):
    x: int = None
    y: int = None
    spectrum: list = None


class Background(NamedTuple):
    title: str = 'background'
    spectrum: list = None


class MicroscopeDevice:
    def __init__(self, stage=None, detection=None):
        notif().addObserver(self, self.stopAcq, "Interrupt acquisition")
        self.lock = Lock()

        self._spec = detection
        self._stage = stage
        self._stagePosition = None
        if self._stage is not None:
            self.resetStagePosition()

        self._width: int = 2
        self._height: int = 2
        self._step: int = 1
        self._stepMeasureUnit: float = 10**3
        self._exposureTime: int = 500
        self._integrationTime = 3000
        self._direction = "same"

        self.waves = []
        # self.dataPoint: list = []
        self.dataMap: list = []
        self.background = []

        self.expositionCounter = 0
        self.integrationCountAcq = 0
        self.liveAcquisitionData = []
        self.integrationTimeAcqRemainder_ms = 0
        self.changeLastExposition = 0
        self.movingIntegrationData = None
        self.countSpectrum = 0
        self.countHeight = 0
        self.countWidth = 0
        self.isAcquisitionDone = False
        self.isAcquiring = False

    def connectStage(self, stage):
        self._stage = stage
        self.resetStagePosition()

    def connectSpec(self, spec):
        self._spec = spec
        if self._spec is not None:
            self.waves = self._spec.wavelengths()[2:]

    def wavelengths(self):
        return self.waves

    @property
    def stagePosition(self):
        return self._stagePosition

    def resetStagePosition(self):
        if self._stage is None:
            raise ConnectionRefusedError("The stage does not seem to be correctly connected.")
        self._stagePosition = self._stage.position()

    def createDataPoint(self, x: int, y: int, spectrum: list):
        return DataPoint(x, y, spectrum)

    def createBackgroundTuple(self, spectrum):
        Background(spectrum=spectrum)

    def validateConditions(self, width=None, height=None, step=None, measureUnit=None):
        if step is None:
            step = self._step
        if measureUnit is None:
            measureUnit = self._stepMeasureUnit
        if width is None:
            width = self._width
        if height is None:
            height = self._height
        if width * step * measureUnit + self._stagePosition[0] > self._stage.xMaxLimit:  # TODO is it in native steps or in microns??
            raise ValueError("The stage does not allow such an important range of motion in width.")
        if height * step * measureUnit + self._stagePosition[1] > self._stage.xMaxLimit:  # TODO is it in native steps or in microns??
            raise ValueError("The stage does not allow such an important range of motion in height.")
        return True

    @property
    def width(self):
        return self._width

    @width.setter
    def width(self, width):
        self.validateConditions(width=width)
        if width <= 0:
            raise ValueError("Images will be mapped strictly with positive coordinates.")
        self._width = width

    @property
    def height(self):
        return self._height

    @height.setter
    def height(self, height):
        self.validateConditions(height=height)
        if height <= 0:
            raise ValueError("Images will be mapped strictly with positive coordinates.")
        self._height = height

    @property
    def step(self):
        return self._step

    @step.setter
    def step(self, step):
        self.validateConditions(step=step)
        if step <= 0:
            raise ValueError("Images will be mapped strictly with positive coordinates.")
        self._step = step

    @property
    def stepMeasureUnit(self):
        return self._stepMeasureUnit

    @stepMeasureUnit.setter
    def stepMeasureUnit(self, unit):
        if unit == 'mm':
            self.validateConditions(measureUnit=10**3)
            self._stepMeasureUnit = 10**3

        elif unit == 'um':
            self.validateConditions(measureUnit=1)
            self._stepMeasureUnit = 1

        elif unit == 'nm':
            self.validateConditions(measureUnit=10**(-3))
            self._stepMeasureUnit = 10**(-3)

    @property
    def exposureTime(self):
        return self._exposureTime

    @exposureTime.setter
    def exposureTime(self, exposition):
        if exposition <= 0:
            raise ValueError("The exposition time cannot be a negative or null value.")
        self._exposureTime = exposition

    @property
    def integrationTime(self):
        return self._integrationTime

    @integrationTime.setter
    def integrationTime(self, integration):
        if integration <= 0:
            raise ValueError("The integration time cannot be a negative or null value.")
        if integration < self._exposureTime:
            raise ValueError("The integration time is necessarily greater or equal to the exposition time.")
        self._integrationTime = integration

    def setDirectionToDefault(self):  # TODO update those functions to only one??
        self._direction = "unidirectional"  # Direction.unidirectional

    def setDirectionToZigzag(self):
        self._direction = "bidirectional"  # Direction.bidirectional

    def resetMovingIntegrationData(self):
        self.movingIntegrationData = None

    def startExposureTime(self, time_in_ms=None, update=True):
        if time_in_ms is not None:
            expositionTime = time_in_ms

        else:
            expositionTime = self._exposureTime

        self._spec.integration_time_micros(expositionTime * 1000)
        if update:
            self.startIntegrationTime()

    def startIntegrationTime(self):
        try:
            if self._integrationTime >= self._exposureTime:
                self.integrationCountAcq = self._integrationTime // self._exposureTime
                self.integrationTimeAcqRemainder_ms = self._integrationTime - (
                        self.integrationCountAcq * self._exposureTime)

            else:
                self.integrationCountAcq = 1

        except ValueError:
            print('nope, wrong value of integration:D')

        if self.integrationTimeAcqRemainder_ms > 3:
            self.movingIntegrationData = RingBuffer(size_max=self.integrationCountAcq + 1)
            self.changeLastExposition = 1

        else:
            self.movingIntegrationData = RingBuffer(size_max=self.integrationCountAcq)
            self.changeLastExposition = 0

    # ACQUISITION
    def spectrumPixelAcquisition(self):
        self.isAcquisitionDone = False

        while not self.isAcquisitionDone:
            self.liveAcquisitionData = self.readDataLive().tolist()
            self.integrateData()
            liveData = np.sum(np.array(self.movingIntegrationData()), 0)

        return liveData

    def acquireBackground(self):
        self.startExposureTime()
        background = self.spectrumPixelAcquisition()
        self.background = background
        notif().postNotification("Background acquired", self, userInfo=background)

    def backgroundData(self):
        return self.background

    def integrateData(self):
        self.isAcquisitionDone = False
        if self.expositionCounter < self.integrationCountAcq - 2:
            self.movingIntegrationData.append(self.liveAcquisitionData)
            self.expositionCounter += 1

        elif self.expositionCounter == self.integrationCountAcq - 2:
            self.movingIntegrationData.append(self.liveAcquisitionData)
            self.expositionCounter += 1
            if self.changeLastExposition:
                self.startExposureTime(self.integrationTimeAcqRemainder_ms, update=False)

        else:
            self.startExposureTime(update=False)
            self.movingIntegrationData.append(self.liveAcquisitionData)
            self.isAcquisitionDone = True
            self.expositionCounter = 0

    def readDataLive(self):
        return self._spec.intensities()[2:]

    def stopAcq(self, *args):
        with self.lock:
            if self.isAcquiring:
                self.isAcquiring = False
                # self.countHeight = 0
                # self.countWidth = 0
                # self.countSpectrum = 0
            else:
                return False
                # raise AssertionError('Sampling already stopped.')
        notif().postNotification("Map acquisition done or interrupted.", self, userInfo={"spectra": self.dataMap})

    # Begin loop
    def begin(self):
        self.validateConditions()
        with self.lock:
            if not self.isAcquiring:
                self.isAcquiring = True
                self.countSpectrum = 0
            else:
                return False
        self.startExposureTime()
        self.points = self._stage.mapPositions(self._width, self._height, self._step*self._stepMeasureUnit,
                                               self._direction)
        self.map()

    def map(self):
        with self.lock:
            inProgress = self.isAcquiring
        while inProgress:
            if self.countSpectrum <= (self._width * self._height):
                dataPoint = self.spectrumPixelAcquisition()
                index = self.points[self.countSpectrum+1]["index"]
                self.dataMap.append(self.createDataPoint(index[0], index[1], dataPoint))
                notif().postNotification("Single acquisition done", self, userInfo={"point_x": index[0],
                                                                                    "point_y": index[1],
                                                                                    "spectrum": dataPoint,
                                                                                    "spectra": self.dataMap})
                position = self.points[self.countSpectrum+1]["position"]
                self.moveStage(position)
                self.countSpectrum += 1
                with self.lock:
                    inProgress = self.isAcquiring
            else:
                inProgress = False
                self.stopAcq()

    def moveStage(self, position):
        self._stage.moveTo(position)
