# # bug fix for numpy.genfromtxt in python 3
# # from https://github.com/numpy/numpy/issues/3184
# import functools
# import io
# import numpy as np
# import sys

# genfromtxt_old = np.genfromtxt
# @functools.wraps(genfromtxt_old)
# def genfromtxt_py3_fixed(f, encoding="utf-8", *args, **kwargs):
#   if isinstance(f, io.TextIOBase):
#     if hasattr(f, "buffer") and hasattr(f.buffer, "raw") and \
#     isinstance(f.buffer.raw, io.FileIO):
#       # Best case: get underlying FileIO stream (binary!) and use that
#       fb = f.buffer.raw
#       # Reset cursor on the underlying object to match that on wrapper
#       fb.seek(f.tell())
#       result = genfromtxt_old(fb, *args, **kwargs)
#       # Reset cursor on wrapper to match that of the underlying object
#       f.seek(fb.tell())
#     else:
#       # Not very good but works: Put entire contents into BytesIO object,
#       # otherwise same ideas as above
#       old_cursor_pos = f.tell()
#       fb = io.BytesIO(bytes(f.read(), encoding=encoding))
#       result = genfromtxt_old(fb, *args, **kwargs)
#       f.seek(old_cursor_pos + fb.tell())
#   else:
#     result = genfromtxt_old(f, *args, **kwargs)
#   return result

# if sys.version_info >= (3,):
#   np.genfromtxt = genfromtxt_py3_fixed

# This causes other problems so just use in python 2


import os
import fnmatch
import copy

import numpy as np
import matplotlib.pyplot as plt
from matplotlib.mlab import griddata
from matplotlib.collections import LineCollection, PolyCollection
from matplotlib import animation

from IPython.display import clear_output

import pyevtk.vtk

import vtk
from vtk.util import numpy_support as vnp

from .quat import Quat


class Mesh(object):
    ### object variables ###
    # meshDir
    # dataDir

    ### MESH DATA ###
    # numElmts(int)
    # numNodes(int)
    # elType(int)
    # elmtCon[numElmnts, 10](int) - Nodes for each element - node numbers are 0 based. row-major
    # nodePos[numNodes, 3](float) - Initial position of each node. col-major
    # numGrains(int) - number of grains in mesh
    # elmtGrain[numElmts](int) - Grain ID of each element - grain ID is 1 based
    # elmtPhase[numElmts](int) - Phase ID of each element - phase ID is 1 based
    # grainOris[numGrains, 3](float) - Initial Euler angles of each grain (Kocks, degrees). col-major

    ### SIMULATION DATA ###
    # numFrames(int) - Number of load steps output
    # numProcs(int) - Number of cores the simulation was run on
    # numSlipSys(int) - Number of slip systems

    # simData - Dictionary of arrays storing loaded simulation data. Arrays stored in column major order.
    # simData['nodePos'][numElmts, 3, numFrames+1](float) - Node positions
    # simData['angle'][numElmts, 3, numFrames+1](float) - Euler angles of each element (Bunge, radians)
    # simData['ori'][numElmts, numFrames+1](Quat) - Quat of orientation of each element

    # misOri

    # Key values for simulation data. Loaded are loaded from file while created are calclated here.
    simDataKeysStatic = (
        "nodePos",
        "nodeVel",
        "angle",
        "hardness",
        "strain",
        "stress",
        "shearRate",
        "effDefRate",
        "effPlasticDefRate",
        "eqvStrain",
        "eqvPlasticStrain",
        "backstress"
    )
    # Any sim data with 3 components is assumed to be a vector unless added to this list
    simDataKeysNotVectorStatic = (
        "angle",
    )

    def __init__(self, name, meshDir="./", dataDir="./"):
        MESH_FILE_EX = "mesh"
        GRAIN_FILE_EX = "grain"
        ORI_FILE_EX = "ori"

        self.meshDir = meshDir if meshDir[-1] == "/" else "{:s}{:s}".format(meshDir, "/")
        self.dataDir = dataDir if dataDir[-1] == "/" else "{:s}{:s}".format(dataDir, "/")

        # open mesh file
        fileName = "{:s}{:s}.{:s}".format(self.meshDir, name, MESH_FILE_EX)
        meshFile = open(fileName, "r")

        # read header
        header = meshFile.readline()
        self.numElmts, self.numNodes, self.elType = (int(x) for x in header.split())

        # read element connectivity data
        self.elmtCon = np.genfromtxt(meshFile,
                                     dtype=int,
                                     max_rows=self.numElmts,
                                     usecols=list(range(1, 11)))

        # read node positions
        self.nodePos = np.asfortranarray(np.genfromtxt(meshFile,
                                                       dtype=float,
                                                       max_rows=self.numNodes,
                                                       usecols=[1, 2, 3]))

        # read number of surfaces
        self.numSurfaces = int(next(meshFile))
        self.surfaces = []
        for i in range(self.numSurfaces):
            numElmts = int(next(meshFile))
            elmtCon = np.genfromtxt(meshFile,
                                    dtype=int,
                                    max_rows=numElmts)
            self.surfaces.append(Surface(self,
                                         numElmts,
                                         np.ascontiguousarray(elmtCon[:, 0]),
                                         np.ascontiguousarray(elmtCon[:, 1:])))

        # close mesh file
        meshFile.close()

        # open grain file
        fileName = "{:s}{:s}.{:s}".format(self.meshDir, name, GRAIN_FILE_EX)
        grainFile = open(fileName, "r")

        # read header
        header = grainFile.readline()
        _, self.numGrains = (int(x) for x in header.split())

        # read grain and phase info for each element
        self.elmtGrain, self.elmtPhase = np.ascontiguousarray(np.genfromtxt(grainFile,
                                                                            dtype=int,
                                                                            unpack=True,
                                                                            max_rows=self.numElmts))

        # close grain file
        grainFile.close()

        # open ori file
        fileName = "{:s}{:s}.{:s}".format(self.meshDir, name, ORI_FILE_EX)
        oriFile = open(fileName, "r")

        # read orientation of each grain (Kocks Euler angles)
        self.grainOris = np.asfortranarray(np.genfromtxt(oriFile,
                                                         dtype=float,
                                                         skip_header=2,
                                                         max_rows=self.numGrains,
                                                         usecols=[0, 1, 2]))

        # close ori file
        oriFile.close()

        # create empty dictionary for storing simulaton data
        self.simData = {}

        # set to none so we tell if element stats have been loaded
        self.elStats = None

        self.simDataKeysDyn = []
        self.simDataKeysNotVectorDyn = []

    @property
    def simDataKeys(self):
        return Mesh.simDataKeysStatic + tuple(self.simDataKeysDyn)

    @property
    def simDataKeysNotVector(self):
        return Mesh.simDataKeysNotVectorStatic + tuple(self.simDataKeysNotVectorDyn)

    def simMetaData(self, dataKey):
        """Produces a dictionary of metadata for the given datakey. Meta data consists of:
            - dataKey (string)
            - includesInitial (bool)
            - numComponents (int)
            - numFrames (int)
            - nodeData (bool): True if node data
            - elmtData (bool): True if element data
            - grainData (bool): True if grain data
            - shape (tuple): shape of data array
            - notVector (bool): True if the data components do not represent a vector

        Args:
            dataKey (string): Sim datakey

        Returns:
            dict: Metadata
        """
        includesInitial = self.simData[dataKey].shape[-1] == self.numFrames + 1
        numComponents = self.simData[dataKey].shape[-2] if len(self.simData[dataKey].shape) == 3 else 1
        nodeData = self.simData[dataKey].shape[0] == self.numNodes
        elmtData = self.simData[dataKey].shape[0] == self.numElmts
        grainData = self.simData[dataKey].shape[0] == self.numGrains
        notVector = dataKey in self.simDataKeysNotVector

        metaData = {
            "dataKey": dataKey,
            "includesInitial": includesInitial,
            "numComponents": numComponents,
            "numFrames": self.simData[dataKey].shape[-1],
            "nodeData": nodeData,
            "elmtData": elmtData,
            "grainData": grainData,
            "shape": self.simData[dataKey].shape,
            "notVector": notVector
        }

        return metaData

    def simDataInfo(self):
        for key, data in self.simData.items():
            print("{:s} {}".format(key, data.shape))

    def setSimParams(self, numProcs=-1, numFrames=-1, numSlipSys=-1):
        if numProcs > 0:
            self.numProcs = numProcs
        if numFrames > 0:
            self.numFrames = numFrames
        if numSlipSys > 0:
            self.numSlipSys = numSlipSys

    def _validateSimDataKey(self, dataKey, fieldType=None):
        if dataKey in self.simData:
            if fieldType is None:
                return True
            else:
                if type(fieldType) is str:
                    fieldType = [fieldType]

                simMetaData = self.simMetaData(dataKey)
                if ("node" in fieldType and simMetaData['nodeData']):
                    return True
                elif ("element" in fieldType and simMetaData['elmtData']):
                    return True
                elif ("grain" in fieldType and simMetaData['grainData']):
                    return True
                else:
                    raise Exception("{:} data required.".format(fieldType))

        elif dataKey in self.simDataKeys:
            raise Exception("{:} data is not loaded.".format(dataKey))
        else:
            raise Exception("\"{:}\" is not a valid sim data key.".format(dataKey))

    def _validateFrameNums(self, frameNums):
        # frameNums: frame or list of frames to run calculation for. -1 for all
        if type(frameNums) != list:
            if type(frameNums) == int:
                if frameNums < 0:
                    frameNums = list(range(self.numFrames + 1))
                else:
                    frameNums = [frameNums]
            else:
                raise Exception("Only an integer or list allowed for frame nums.")
        return frameNums

    def createSimData(self, dataKey, data, isNotVector=False):
        self.simDataKeysDyn.append(dataKey)
        self.simData[dataKey] = np.asfortranarray(data)
        if isNotVector:
            self.simDataKeysNotVectorDyn.append(dataKey)

    def loadFrameData(self, dataName, initialIncd, usecols=None, numProcs=-1, numFrames=-1, numSlipSys=-1):
        self.setSimParams(numProcs=numProcs, numFrames=numFrames, numSlipSys=numSlipSys)

        # +1 if initial values are also stored
        numFrames = (self.numFrames + 1) if initialIncd else self.numFrames
        singleData = False

        loadedData = []
        for i in range(self.numProcs):
            # load data per processor
            fileName = "{:s}post.{:s}.{:d}".format(self.dataDir, dataName, i)
            loadedData.append(np.loadtxt(fileName, dtype=float, comments="%", usecols=usecols, unpack=True))

            if len(loadedData[i].shape) == 1:
                # scalar data
                singleData = True
                # reshape into 2d array with 2nd dim for each frame
                cols, = loadedData[i].shape
                perFrame = cols / numFrames
                loadedData[i] = np.reshape(loadedData[i], (perFrame, numFrames), order='F')
            else:
                # vector data
                # reshape into 3d array with 3rd dim for each frame
                rows, cols = loadedData[i].shape
                perFrame = cols / numFrames
                loadedData[i] = np.reshape(loadedData[i], (rows, perFrame, numFrames), order='F')

        # concatenate data from all processors into one array and transpose first 2 axes if vectr data
        # make data contiguous in memory in column major order
        if singleData:
            return np.asfortranarray(np.concatenate(loadedData, axis=0))
        else:
            return np.asfortranarray(np.transpose(np.concatenate(loadedData, axis=1), axes=(1, 0, 2)))

    def loadSimData(self, dataKeys, forceLoad=False, numProcs=-1, numFrames=-1, numSlipSys=-1):
        self.setSimParams(numProcs=numProcs, numFrames=numFrames, numSlipSys=numSlipSys)

        simDataLoadFunctions = {
            "nodePos": self.loadNodePosData,
            "nodeVel": self.loadNodeVelData,
            "angle": self.loadAngleData,
            "hardness": self.loadHardnessData,
            "strain": self.loadStrainData,
            "stress": self.loadStressData,
            "shearRate": self.loadShearRateData,
            "effDefRate": self.loadEffDefRateData,
            "effPlasticDefRate": self.loadEffPlasticDefRateData,
            "eqvStrain": self.loadEqvStrainData,
            "eqvPlasticStrain": self.loadEqvPlasticStrainData,
            "backstress": self.loadBackstressData
        }

        for dataKey in dataKeys:
            if dataKey in Mesh.simDataKeysStatic:
                if forceLoad or (dataKey not in self.simData):
                    simDataLoadFunctions[dataKey]()
                    print("Finished loading {:} data.".format(dataKey))
                else:
                    print("{:} data already loaded.".format(dataKey))
            else:
                print("\"{:}\" is not a valid sim data key.".format(dataKey))

    # load node positions for each frame of the simulation
    def loadNodePosData(self, numProcs=-1, numFrames=-1):
        self.simData['nodePos'] = self.loadFrameData("adx",
                                                     True,
                                                     numProcs=numProcs,
                                                     numFrames=numFrames)

    def loadNodeVelData(self, numProcs=-1, numFrames=-1):
        self.simData['nodeVel'] = self.loadFrameData("advel",
                                                     True,
                                                     numProcs=numProcs,
                                                     numFrames=numFrames)

    def loadAngleData(self, numProcs=-1, numFrames=-1):
        self.simData['angle'] = self.loadFrameData("ang",
                                                   True,
                                                   usecols=[1, 2, 3],
                                                   numProcs=numProcs,
                                                   numFrames=numFrames)

        # Covert to Bunge rep. in radians (were Kocks in degrees)
        self.simData['angle'] *= (np.pi / 180)
        self.simData['angle'][:, 0, :] += np.pi / 2
        self.simData['angle'][:, 2, :] *= -1
        self.simData['angle'][:, 2, :] += np.pi / 2

        # construct quat array
        oriData = np.empty([self.simData['angle'].shape[0], self.simData['angle'].shape[2]], dtype=Quat)

        for i in range(self.numFrames + 1):
            for j, eulers in enumerate(self.simData['angle'][..., i]):
                oriData[j, i] = Quat(eulers[0], eulers[1], eulers[2])

        self.createSimData("ori", oriData)

    def loadHardnessData(self, numProcs=-1, numFrames=-1, numSlipSys=-1):
        self.simData['hardness'] = self.loadFrameData("crss",
                                                      True,
                                                      numProcs=numProcs,
                                                      numFrames=numFrames,
                                                      numSlipSys=numSlipSys)

    def loadStrainData(self, numProcs=-1, numFrames=-1):
        self.simData['strain'] = self.loadFrameData("strain",
                                                    False,
                                                    numProcs=numProcs,
                                                    numFrames=numFrames)

    def loadStressData(self, numProcs=-1, numFrames=-1):
        self.simData['stress'] = self.loadFrameData("stress",
                                                    False,
                                                    numProcs=numProcs,
                                                    numFrames=numFrames)

    def loadShearRateData(self, numProcs=-1, numFrames=-1, numSlipSys=-1):
        self.simData['shearRate'] = self.loadFrameData("gammadot",
                                                       False,
                                                       numProcs=numProcs,
                                                       numFrames=numFrames,
                                                       numSlipSys=numSlipSys)

    def loadEffDefRateData(self, numProcs=-1, numFrames=-1):
        self.simData['effDefRate'] = self.loadFrameData("deff",
                                                        False,
                                                        numProcs=numProcs,
                                                        numFrames=numFrames)

    def loadEffPlasticDefRateData(self, numProcs=-1, numFrames=-1):
        self.simData['effPlasticDefRate'] = self.loadFrameData("dpeff",
                                                               False,
                                                               numProcs=numProcs,
                                                               numFrames=numFrames)

    def loadEqvStrainData(self, numProcs=-1, numFrames=-1):
        self.simData['eqvStrain'] = self.loadFrameData("eqstrain",
                                                       False,
                                                       numProcs=numProcs,
                                                       numFrames=numFrames)

    def loadEqvPlasticStrainData(self, numProcs=-1, numFrames=-1):
        self.simData['eqvPlasticStrain'] = self.loadFrameData("eqplstrain",
                                                              False,
                                                              numProcs=numProcs,
                                                              numFrames=numFrames)

    def loadBackstressData(self, numProcs=-1, numFrames=-1):
        self.simData['backstress'] = self.loadFrameData("backstress",
                                                        False,
                                                        numProcs=numProcs,
                                                        numFrames=numFrames)

    def loadMeshElStatsData(self, name):
        # open mesh stats file
        ELSTAT_FILE_EX = "stelt3d"
        fileName = "{:s}{:s}.{:s}".format(self.meshDir, name, ELSTAT_FILE_EX)
        elStatFile = open(fileName, "r")

        # read grain and phase info for each element
        elStats = np.loadtxt(elStatFile)

        if elStats.shape[0] != self.numElmts:
            print("Problem with element stats file. Missing element data.")
        else:
            self.elStats = np.asfortranarray(elStats)

        self.meshElStatNames = ["ID",
                                "coord-x", "coord-y", "coord-z",
                                "elset (grains)", "partition", "volume",
                                "2dmeshp-x", "2dmeshp-y", "2dmeshp-z",
                                "2dmeshd",
                                "2dmeshv-x", "2dmeshv-y", "2dmeshv-z",
                                "2dmeshn-x", "2dmeshn-y", "2dmeshn-z"]

    def constructVtkMesh(self):
        """Create VTK mesh using initial (undeformaed) node positions

        Returns:
            vtkUnstructuredGrid: VTK mesh
        """
        CON_ORDER = [0, 2, 4, 9, 1, 3, 5, 6, 7, 8]  # corners, then midpoints
        ELMT_TYPE = 24

        # create vtk point array for node positions
        points = vtk.vtkPoints()
        for coord in self.nodePos:
            points.InsertNextPoint(coord)

        # create vtk unstructured grid and assign point array
        uGrid = vtk.vtkUnstructuredGrid()
        uGrid.SetPoints(points)

        # add cells to unstructured grid
        con = vtk.vtkIdList()
        for elmtCon in self.elmtCon:
            con.Reset()
            for pointID in elmtCon[CON_ORDER]:
                con.InsertNextId(pointID)

            uGrid.InsertNextCell(ELMT_TYPE, con)

        return uGrid

    def calcGradient(self, inDataKey, outDataKey):
        """Calculated gradient of simulation data wrt initial coordinates

        Args:
            inDataKey (string): Sim data key to caluclate gradient of
            outDataKey (string): Sim data key to store result
        """

        # validate input data
        self._validateSimDataKey(inDataKey, fieldType="node")

        # create array to store gradient
        simMetaData = self.simMetaData(inDataKey)
        inDataShape = simMetaData['shape']
        if simMetaData['numComponents'] == 1:
            gradient = np.empty((inDataShape[0], 3, inDataShape[1]))
        else:
            gradient = np.empty((inDataShape[0], 3 * inDataShape[1], inDataShape[2]))

        # create VTK mesh
        uGrid = self.constructVtkMesh()

        numFrames = self.simData[inDataKey].shape[-1]
        for i in range(numFrames):
            # add point data to unstructured grid
            vtkData = vnp.numpy_to_vtk(np.ascontiguousarray(self.simData[inDataKey][..., i]))
            vtkData.SetName(inDataKey)
            uGrid.GetPointData().AddArray(vtkData);

            # apply vtk gradient filter
            gradFilter = vtk.vtkGradientFilter()
            gradFilter.SetInputDataObject(uGrid)
            gradFilter.SetInputScalars(vtk.vtkDataObject.FIELD_ASSOCIATION_POINTS, inDataKey)
            gradFilter.Update()

            # collect output
            gradient[:, :, i] = vnp.vtk_to_numpy(
                gradFilter.GetOutput().GetPointData().GetArray('Gradients')
            )

            # Garbage collection before next frame
            gradFilter = None
            uGrid.GetPointData().RemoveArray(inDataKey);
            vtkData = None

        self.createSimData(outDataKey, gradient)

    def calcMisori(self, frameNums):
        frameNums = self._validateFrameNums(frameNums)

        # create arrays to store misori data
        self.createSimData("misOri", np.zeros(self.simData['ori'].shape, dtype=float))
        self.createSimData("avMisOri", np.zeros([self.numGrains, self.simData['ori'].shape[1]], dtype=float))
        self.createSimData("maxMisOri", np.zeros([self.numGrains, self.simData['ori'].shape[1]], dtype=float))
        self.createSimData("avOri", np.zeros([self.numGrains, self.simData['ori'].shape[1]], dtype=Quat))

        symmetry = 'cubic'
        # Loop over frames
        for i, frameNum in enumerate(frameNums):
            # Loop over grains
            for grainID in range(1, self.numGrains + 1):
                # select grains based on grainID
                selected = np.where(self.elmtGrain == grainID)[0]
                quats = self.simData['ori'][selected, frameNum]

                quatCompsSym = Quat.calcSymEqvs(quats, symmetry)

                averageOri = Quat.calcAverageOri(quatCompsSym)

                misOriArray, _ = Quat.calcMisOri(quatCompsSym, averageOri)

                self.simData['misOri'][selected, frameNum] = misOriArray
                self.simData['avMisOri'][grainID - 1, frameNum] = misOriArray.mean()
                self.simData['maxMisOri'][grainID - 1, frameNum] = misOriArray.min()
                self.simData['avOri'][grainID - 1, frameNum] = averageOri

            clear_output()
            print("Frame {:d} of {:d} done.".format(i + 1, len(frameNums)))

        self.simData['misOri'][:, frameNums] = np.arccos(self.simData['misOri'][:, frameNums]) * 360 / np.pi
        self.simData['avMisOri'][:, frameNums] = np.arccos(self.simData['avMisOri'][:, frameNums]) * 360 / np.pi
        self.simData['maxMisOri'][:, frameNums] = np.arccos(self.simData['maxMisOri'][:, frameNums]) * 360 / np.pi

    def calcGrainAverage(self, inDataKey, outDataKey=None):
        # validate input data
        self._validateSimDataKey(inDataKey, fieldType="element")

        inMetaData = self.simMetaData(inDataKey)
        outDataShape = (self.numGrains,) + inMetaData['shape'][1:]

        grainData = np.empty(outDataShape)

        for i in range(self.numGrains):
            grainData[i] = self.simData[inDataKey][self.elmtGrain == (i + 1), ...].mean(axis=0)

        if outDataKey is None:
            return grainData
        else:
            self.createSimData(outDataKey, grainData, isNotVector=inMetaData['notVector'])
            return

    def writeVTU(self, fileName, frameNums, outputs, times=None, useInitialNodePos=True):
        """Write data out to VTK compatible files. Files are output to the data directory.

        Args:
            fileName (string): Base name of output files.
            frameNums (lst(int)): The simlation frames to output. Either an integer for a single
                                  frame, a list of ints for many or -1 for all. 0 is intial and 1 is first sim output
            outputs (list(string)): The properties to be output. Current options are misOri, gammadot,
                                    backstress and elStats.
            times (list(float), optional): The time at each frame (only used for labeling)
            useInitialNodePos (bool, optional): If false uses updateded node positions from each frame. Default: True.
        """
        # Constants
        CON_ORDER = [0, 2, 4, 9, 1, 3, 5, 6, 7, 8]  # corners, then midpoints
        ELMT_TYPE = 24
        FILE_POSTFIX = ""

        # create arrays for element type, conneectivity offset and node positions
        cell_types = np.empty(self.numElmts, dtype='uint8')
        # cell_types[:] = pyevtk.vtk.VtkQuadraticTetra.tid
        cell_types[:] = ELMT_TYPE

        offsets = np.arange(start=10, stop=10 * (self.numElmts + 1), step=10, dtype=int)

        if useInitialNodePos:
            x = self.nodePos[:, 0]
            y = self.nodePos[:, 1]
            z = self.nodePos[:, 2]

        # check frameNums and times are valid
        frameNums = self._validateFrameNums(frameNums)
        if times is None:
            times = frameNums

        # validate outputs
        includeElStats = False
        # list of tuples includng validated name, if includes initial data,
        # number of data fields and if nodal or elemental data (true for nodal)
        simMetaDatas = []
        for dataKey in outputs:
            if dataKey in self.simData:
                # add metadata to list. node data fields to the start and element data fields to the end
                simMetaData = self.simMetaData(dataKey)
                if simMetaData['nodeData']:
                    simMetaDatas.insert(0, simMetaData)
                else:
                    simMetaDatas.append(simMetaData)

            elif dataKey == "elStats":
                if self.elStats is not None:
                    includeElStats = True
                else:
                    print("Element stats have not been loaded")

            elif dataKey in self.simDataKeys:
                print("{:} data is not loaded.".format(dataKey))

            else:
                print("\"{:}\" is not a valid output data key.".format(dataKey))

        # open vtk group file
        fileNameFull = "{:s}{:s}{:s}".format(self.dataDir, fileName, FILE_POSTFIX)
        print(fileNameFull)
        vtgFile = pyevtk.vtk.VtkGroup(fileNameFull)

        for frameNum in frameNums:
            # write frame vtu file path to vtk group file (this needed modification to
            # evtk module to write paths not relative to the current working directory)
            fileNameFull = "{:s}{:s}.{:d}.vtu".format(fileName, FILE_POSTFIX, frameNum)
            vtgFile.addFile(fileNameFull, times[frameNum], relToCWD=False)

            # open vtu file and write root elements (node positions, element connectivity and type)
            fileNameFull = "{:s}{:s}{:s}.{:d}".format(self.dataDir, fileName, FILE_POSTFIX, frameNum)
            vtuFile = pyevtk.vtk.VtkFile(fileNameFull, pyevtk.vtk.VtkUnstructuredGrid)
            vtuFile.openGrid()
            vtuFile.openPiece(ncells=self.numElmts, npoints=self.numNodes)

            if not useInitialNodePos:
                x = self.simData['nodePos'][:, 0, frameNum]
                y = self.simData['nodePos'][:, 1, frameNum]
                z = self.simData['nodePos'][:, 2, frameNum]

            vtuFile.openElement("Points")
            vtuFile.addData("points", (x, y, z))
            vtuFile.closeElement("Points")

            vtuFile.openElement("Cells")
            # vtuFile.addData("connectivity", self.elmtCon.flatten())
            vtuFile.addHeader("connectivity", self.elmtCon.dtype.name, self.elmtCon.size, 1)
            vtuFile.addData("offsets", offsets)
            vtuFile.addData("types", cell_types)
            vtuFile.closeElement("Cells")

            # Add headers of simulation point data
            vtuFile.openElement("PointData")

            # loop over outputs adding headers if node data
            for simMetaData in simMetaDatas:
                if (simMetaData['includesInitial'] or (frameNum > 0)) and simMetaData['nodeData']:
                    dataKey = simMetaData['dataKey']
                    trueFrameNum = frameNum if simMetaData['includesInitial'] else frameNum - 1
                    # check if single or multiple variable output
                    if simMetaData['numComponents'] == 1:
                        # add single header to file
                        vtuFile.addHeader(
                            dataKey,
                            self.simData[dataKey].dtype.name,
                            self.simData[dataKey][:, trueFrameNum].size,
                            1
                        )
                    elif (simMetaData['numComponents'] == 3) and (dataKey not in self.simDataKeysNotVector):
                        # add single header to file with 3 components for vector
                        vtuFile.addHeader(
                            dataKey,
                            self.simData[dataKey].dtype.name,
                            self.simData[dataKey][:, 0, trueFrameNum].size,
                            3
                        )
                    else:
                        # add multiple headers to file
                        for i in range(simMetaData['numComponents']):
                            vtuFile.addHeader(
                                "{:s} {:d}".format(dataKey, i + 1),
                                self.simData[dataKey].dtype.name,
                                self.simData[dataKey][:, i, trueFrameNum].size,
                                1
                            )

            vtuFile.closeElement("PointData")

            # Add headers of simulation cell data
            vtuFile.openElement("CellData")

            # loop over outputs adding headers if element data
            for simMetaData in simMetaDatas:
                if (simMetaData['includesInitial'] or (frameNum > 0)) and simMetaData['elmtData']:
                    dataKey = simMetaData['dataKey']
                    trueFrameNum = frameNum if simMetaData['includesInitial'] else frameNum - 1
                    # check if single or multiple variable output
                    if simMetaData['numComponents'] == 1:
                        # add single header to file
                        vtuFile.addHeader(
                            dataKey,
                            self.simData[dataKey].dtype.name,
                            self.simData[dataKey][:, trueFrameNum].size,
                            1
                        )
                    elif (simMetaData['numComponents'] == 3) and (dataKey not in self.simDataKeysNotVector):
                        # add single header to file with 3 components for vector
                        vtuFile.addHeader(
                            dataKey,
                            self.simData[dataKey].dtype.name,
                            self.simData[dataKey][:, 0, trueFrameNum].size,
                            3
                        )
                    else:
                        # add multiple headers to file
                        for i in range(simMetaData['numComponents']):
                            vtuFile.addHeader(
                                "{:s} {:d}".format(dataKey, i + 1),
                                self.simData[dataKey].dtype.name,
                                self.simData[dataKey][:, i, trueFrameNum].size,
                                1
                            )

            # write element stats headers if required and on first frame
            if includeElStats and (frameNum == 0):
                for i in range(self.elStats.shape[1]):
                    vtuFile.addHeader("Element stat - {:}".format(self.meshElStatNames[i]),
                                      self.elStats.dtype.name,
                                      self.elStats[:, i].size,
                                      1)

            vtuFile.closeElement("CellData")

            vtuFile.closePiece()
            vtuFile.closeGrid()

            # add actual data to file
            # mesh defiition stuff
            vtuFile.appendData((x, y, z))
            vtuFile.appendData(self.elmtCon[:, CON_ORDER].flatten()).appendData(offsets).appendData(cell_types)

            # simulation data
            # loop over outputs adding data
            for simMetaData in simMetaDatas:
                if simMetaData['includesInitial'] or (frameNum > 0):
                    dataKey = simMetaData['dataKey']
                    trueFrameNum = frameNum if simMetaData['includesInitial'] else frameNum - 1
                    # check if single or multiple variable output
                    if simMetaData['numComponents'] == 1:
                        # add single data set
                        vtuFile.appendData(self.simData[dataKey][:, trueFrameNum])

                    elif (simMetaData['numComponents'] == 3) and (dataKey not in self.simDataKeysNotVector):
                        # add vector data set
                        vtuFile.appendData((
                            self.simData[dataKey][:, 0, trueFrameNum],
                            self.simData[dataKey][:, 1, trueFrameNum],
                            self.simData[dataKey][:, 2, trueFrameNum]
                        ))

                    else:
                        # add multiple data sets
                        for i in range(simMetaData['numComponents']):
                            vtuFile.appendData(self.simData[dataKey][:, i, trueFrameNum])

            # write element stats data if required and on first frame
            if includeElStats and (frameNum == 0):
                for i in range(self.elStats.shape[1]):
                    vtuFile.appendData(self.elStats[:, i])

            vtuFile.save()

        vtgFile.save()

    @staticmethod
    def combineFiles(baseDir, inDirs, outDir):
        """Combine output files from multiple simulations. If a simulation was run in smaller parts

        Args:
            baseDir (string): Base directory of whole simulation. No trailing slash
            inDirs (List(string)): List of simulation directory names to
                                   combine (baseDir/inDir). No trailing slash
            outDir (string): Directory to output combined files to (baseDir/outDir)
        """
        # no trailing slashes on directory names and inDirs is a list
        fileNames = []
        for fileName in os.listdir("{:s}/{:s}".format(baseDir, inDirs[0])):
            if (fnmatch.fnmatch(fileName, "post.*") and
                fileName != "post.conv" and fileName != "post.stats" and not
                    (fnmatch.fnmatch(fileName, "post.debug.*") or
                     fnmatch.fnmatch(fileName, "post.log.*") or
                     fnmatch.fnmatch(fileName, "post.restart.*")
                     )):

                fileNames.append(fileName)

        if not os.path.isdir("{:s}/{:s}".format(baseDir, outDir)):
            os.mkdir("{:s}/{:s}".format(baseDir, outDir))

        for fileName in fileNames:
            outFile = open("{:s}/{:s}/{:s}".format(baseDir, outDir, fileName), 'w')

            for inDir in inDirs:
                inFile = open("{:s}/{:s}/{:s}".format(baseDir, inDir, fileName), 'r')
                for line in inFile:
                    outFile.write(line)
                inFile.close()

            outFile.close()


class Surface(object):

    def __init__(self, mesh, numElmts, elmtIDs, elmtCon):
        self.mesh = mesh
        self.numElmts = numElmts
        self.elmtIDs = elmtIDs          # Mesh global element IDs
        self.elmtCon = elmtCon          # Mesh global element IDs

        self.nodeIDs = np.unique(self.elmtCon.flatten())    # Mesh global node IDs
        self.numNodes = len(self.nodeIDs)

        self.forceCalc = False          # Set to true to force recalcualtion of all below
        self._elmtEdges = None
        self._elmtNeighbours = None
        self._elmtNeighbourEdges = None
        self._grainEdges = None
        self._meshEdges = None

    @property
    def elmtGrain(self):
        """Returns an aray of grain IDs for elements in the surface (note grain IDs are 1 based)
        """
        return self.mesh.elmtGrain[self.elmtIDs]

    @property
    def elmtEdges(self):
        # Create array with 3 edges of each element
        # Egdes are ordered tuples of 2 nodeIDs i.e (i, j) where j>i. nodeIDs are global for the mesh
        # Only use corner nodes (0,2,4), no mid edge nodes
        # Calculate only if not done before
        if (self._elmtEdges is None) or self.forceCalc:
            elmtEdges = np.empty((self.numElmts, 3), dtype=tuple)

            for i, elmtCon in enumerate(self.elmtCon):
                elmtEdges[i, 0] = (elmtCon[0], elmtCon[2]) if elmtCon[2] > elmtCon[0] else (elmtCon[2], elmtCon[0])
                elmtEdges[i, 1] = (elmtCon[2], elmtCon[4]) if elmtCon[4] > elmtCon[2] else (elmtCon[4], elmtCon[2])
                elmtEdges[i, 2] = (elmtCon[4], elmtCon[0]) if elmtCon[0] > elmtCon[4] else (elmtCon[0], elmtCon[4])

            self._elmtEdges = elmtEdges

        return self._elmtEdges

    @property
    def elmtNeighbours(self):
        # Create element neighbour list. Stored as tuples of 2 local surface elementIDs
        # Neighbour edges are the edges that are adjacent. global nodeIDs
        # Might be faster to find elements with a shared node first but this isn't too slow
        if (self._elmtNeighbours is None) or (self._elmtNeighbourEdges is None) or self.forceCalc:
            neighbours = []
            neighbourEdges = []
            elmtEdges = self.elmtEdges

            # Loop over elements to compare each with all others
            for i in range(self.numElmts):
                for j in range(i + 1, self.numElmts):
                    # check if any of the edges are the same
                    if elmtEdges[i, 0] == elmtEdges[j, 0]:
                        neighbours.append((i, j))
                        neighbourEdges.append(elmtEdges[i, 0])
                        continue
                    if elmtEdges[i, 0] == elmtEdges[j, 1]:
                        neighbours.append((i, j))
                        neighbourEdges.append(elmtEdges[i, 0])
                        continue
                    if elmtEdges[i, 0] == elmtEdges[j, 2]:
                        neighbours.append((i, j))
                        neighbourEdges.append(elmtEdges[i, 0])
                        continue
                    if elmtEdges[i, 1] == elmtEdges[j, 0]:
                        neighbours.append((i, j))
                        neighbourEdges.append(elmtEdges[i, 1])
                        continue
                    if elmtEdges[i, 1] == elmtEdges[j, 1]:
                        neighbours.append((i, j))
                        neighbourEdges.append(elmtEdges[i, 1])
                        continue
                    if elmtEdges[i, 1] == elmtEdges[j, 2]:
                        neighbours.append((i, j))
                        neighbourEdges.append(elmtEdges[i, 1])
                        continue
                    if elmtEdges[i, 2] == elmtEdges[j, 0]:
                        neighbours.append((i, j))
                        neighbourEdges.append(elmtEdges[i, 2])
                        continue
                    if elmtEdges[i, 2] == elmtEdges[j, 1]:
                        neighbours.append((i, j))
                        neighbourEdges.append(elmtEdges[i, 2])
                        continue
                    if elmtEdges[i, 2] == elmtEdges[j, 2]:
                        neighbours.append((i, j))
                        neighbourEdges.append(elmtEdges[i, 2])
                        continue

            self._elmtNeighbours = neighbours
            self._elmtNeighbourEdges = neighbourEdges

        return self._elmtNeighbours, self._elmtNeighbourEdges

    @property
    def grainEdges(self):
        # Find grain egdes. Loop over neighbours and if grainID is different add the edges to the list
        if (self._grainEdges is None) or self.forceCalc:
            grainEdges = []
            neighbours, neighbourEdges = self.elmtNeighbours

            for i, neighbour in enumerate(neighbours):
                grainIDs = (self.mesh.elmtGrain[self.elmtIDs[neighbour[0]]],
                            self.mesh.elmtGrain[self.elmtIDs[neighbour[1]]])

                if grainIDs[0] != grainIDs[1]:
                    grainEdges.append(neighbourEdges[i])

            self._grainEdges = np.array(grainEdges)

        return self._grainEdges

    @property
    def meshEdges(self):
        if (self._meshEdges is None) or self.forceCalc:
            meshEdges = np.empty((3 * self.numElmts, 2), dtype=int)

            for i, edges in enumerate(self.elmtEdges):
                for j, edge in enumerate(edges):
                    meshEdges[3 * i + j] = edge

            self._meshEdges = np.unique(meshEdges, axis=0)

        return self._meshEdges

    def _2dAxes(self, surfaceNormal):
        if surfaceNormal == 'x':
            axes = (1, 2)
        elif surfaceNormal == 'y':
            axes = (0, 2)
        elif surfaceNormal == 'z':
            axes = (0, 1)
        else:
            raise Exception("Surface normal must be x, y or z.")

        return axes

    def plotGBs(self, surfaceNormal, colour=None, linewidth=None, ax=None):
        lc = LineCollection(self.mesh.nodePos[:, self._2dAxes(surfaceNormal)][self.grainEdges],
                            colors=colour,
                            linewidths=linewidth)
        if ax is None:
            plt.gca().add_collection(lc)
        else:
            ax.add_collection(lc)

    def plotMesh(self, surfaceNormal, colour=None, linewidth=None, ax=None):
        lc = LineCollection(self.mesh.nodePos[:, self._2dAxes(surfaceNormal)][self.meshEdges],
                            colors=colour,
                            linewidths=linewidth)
        if ax is None:
            plt.gca().add_collection(lc)
        else:
            ax.add_collection(lc)

    def plotSimData(self, dataKey, surfaceNormal, plotType="image", component=0,
                    frameNum=1, plotGBs=False, plotMesh=False, label="", cmap="viridis",
                    vmin=None, vmax=None, invertData=False, returnFig=False, inputFig=None):
        # validate input data
        self.mesh._validateSimDataKey(dataKey)
        simMetaData = self.mesh.simMetaData(dataKey)
        if not simMetaData['includesInitial']:
            if frameNum > 0:
                frameNum -= 1
            else:
                raise Exception("{:s} does not have initial data.".format(dataKey))

        axes = self._2dAxes(surfaceNormal)

        # collect unstructured coordinates
        axes = self._2dAxes(surfaceNormal)
        cX = self.mesh.nodePos[self.nodeIDs, axes[0]]
        cY = self.mesh.nodePos[self.nodeIDs, axes[1]]

        # set area
        extent = (cX.min(), cX.max(), cY.min(), cY.max())

        # if input fig is provided then update it else create a new one
        if inputFig is None:
            fig, ax = plt.subplots()
        else:
            fig, ax, img = inputFig

        if simMetaData['elmtData'] or simMetaData['grainData']:
            if simMetaData['elmtData']:
                if simMetaData['numComponents'] == 1:
                    cData = self.mesh.simData[dataKey][self.elmtIDs, frameNum]
                else:
                    cData = self.mesh.simData[dataKey][self.elmtIDs, component, frameNum]
            else:
                if simMetaData['numComponents'] == 1:
                    cData = self.mesh.simData[dataKey][self.elmtGrain - 1, frameNum]
                else:
                    cData = self.mesh.simData[dataKey][self.elmtGrain - 1, component, frameNum]

            if invertData:
                cData = -cData

            pc = PolyCollection(self.mesh.nodePos[:, axes][self.elmtCon[:, (0, 2, 4)]],
                                cmap=cmap, antialiaseds=False)
            pc.set_clim(vmin=vmin, vmax=vmax)
            pc.set_array(cData)

            ax.add_collection(pc)
            fig.colorbar(pc, label=label)

        elif simMetaData['nodeData']:
            if simMetaData['numComponents'] == 1:
                cData = self.mesh.simData[dataKey][self.nodeIDs, frameNum]
            else:
                cData = self.mesh.simData[dataKey][self.nodeIDs, component, frameNum]

            if invertData:
                cData = -cData

            # calculate number of points in each direction based on shape of surface
            shape = np.array((extent[1] - extent[0], extent[3] - extent[2]))
            scale = self.numNodes / (shape[0] * shape[1])
            numPoints = 2 * np.sqrt(scale) * shape
            numPoints = numPoints.round()

            # create grid and interpolate data to it
            gX, gY = np.mgrid[extent[0]:extent[1]:numPoints[0] * 1j, extent[2]:extent[3]:numPoints[1] * 1j]
            gData = griddata(cX, cY, cData, gX, gY, interp='linear')

            if inputFig is None:
                if plotType == "contour":
                    contours = np.linspace(vmin, vmax, 9)
                    img = ax.contourf(gX, gY, gData, contours, cmap=cmap, vmin=vmin, vmax=vmax)
                else:
                    img = ax.imshow(gData.transpose(), origin='lower', cmap=cmap, vmin=vmin, vmax=vmax,
                                    extent=extent, interpolation='nearest')

                fig.colorbar(img, label=label)
            else:
                if plotType == "contour":
                    img.set_data(gData)
                else:
                    img.set_data(gData.transpose())

        if plotGBs:
            self.plotGBs(surfaceNormal, ax=ax, colour="white", linewidth=1)
        if plotMesh:
            self.plotMesh(surfaceNormal, ax=ax, colour="white", linewidth=0.2)

        if inputFig is None:
            ax.set_xticks([])
            ax.set_yticks([])
            ax.set_xlim((extent[0], extent[1]))
            ax.set_ylim((extent[2], extent[3]))
            ax.set_aspect('equal')

        if returnFig:
            return fig, ax, img
        else:
            fig.show()

    def animateSimData(self, dataKey, surfaceNormal, plotType="image", component=0,
                       plotGBs=False, plotMesh=False, label="", cmap="viridis",
                       vmin=None, vmax=None, invertData=False, saveVid=False):

        simMetaData = self.mesh.simMetaData(dataKey)
        if simMetaData['includesInitial']:
            frames = range(simMetaData['numFrames'])
        else:
            frames = range(1, simMetaData['numFrames'] + 1)

        fig, ax, img = self.plotSimData(dataKey, surfaceNormal, plotType=plotType, component=component,
                                        frameNum=frames[0], plotGBs=plotGBs, plotMesh=plotMesh, label=label, cmap=cmap,
                                        vmin=vmin, vmax=vmax, invertData=invertData, returnFig=True)

        def animate(frameNum):
            _, _, rtnImg = self.plotSimData(dataKey, surfaceNormal, plotType=plotType, component=component,
                                            frameNum=frameNum, plotGBs=plotGBs, plotMesh=plotMesh, label=label,
                                            cmap=cmap, vmin=vmin, vmax=vmax, invertData=invertData, returnFig=True,
                                            inputFig=(fig, ax, img))
            return rtnImg,

        # The FuncAnimation must not go out of scope so assigned to an instance variable
        self.anim = animation.FuncAnimation(fig, animate, frames=frames, interval=1000, blit=True)

        if saveVid:
            fileNameFull = "{:s}{:s}-comp{:d}.gif".format(self.mesh.dataDir, dataKey, component)
            print(fileNameFull)

            self.anim.save(fileNameFull, dpi=200, writer='imagemagick')

            # self.anim.save(fileNameFull, fps=1, writer="ffmpeg", bitrate=-1,
            #                codec="libx264", extra_args=['-pix_fmt', 'yuv420p'])
