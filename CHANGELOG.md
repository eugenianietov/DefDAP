# Change Log

## Current

### Added
- Add EBSD file writing to CTF format.
- Add cretation of EBSD maps from runtime data.
- Add method to generate all slip systems in a family from a single system.
- Add grouping of slip systems by family.
- Add mechanism to define default parameter values stored in single loaction.
- Add basic filtering for HRDIC maps.
  - Filtering based on threshold of effective shear strain and subsequent binary dilation.
  - All current DefDAP functions work with NaN, but the RDR might not work as expected.
- New grain finding algorithm for HRDIC map which warps the EBSD grain map.
- Add `addLegend` command to add a marker size legend to a pole plot.
- Add misorientation calculation between neighbouring EBSD grains.
- Add a `BoundarySegment` class to represent a section of grain boundary between 2 grain in an EBSD map. Objects of this class are assigned to edges of the neighbour network and contain all the boundary points between the grains.
- Add Kuwahara filter for EBSD map noise reduction.
- Add shape property to maps.
- Read EBSD phases from file.
- Add classes to represent phases and crystal structures.

### Changed
- Update progress reporting to print elapsed time.
- Speed up grain finding algorithm.
- Update plot IPF and Euler map to consider multiple phasess.
- Drop support of python 3.5.
- Update boundary and grain finding to consider phase boundaries. 
- Assign a phase to each grain in an EBSD map.
- Change equality and hash of slip systems, slip plane and direction must now be equal (-ve allowed but not different norms).
- Update slip system plane and direction lables  to have overbars - very fancy.
- Updates to example notebook.
- Change docs over to readthedocs.
- Move version number to own file.
- Update neighbour network to use grains as nodes.
- Store grainID in grain objects.
- Split plotGrainDataMap into separate array construction and plotting function.
- Update neighbour network construction to use new EBSD boundary definition.
- Update flood fill algorithm for grain finding in a EBSD map.
- Vertical and horizontal boundary pixels are now considered separately.
- Load phases from CTF file.

### Fixed 
- Fix bug with comment and blank lines when reading CPR files.
- Fixes to quat class:
  - Construction from axis/angle now in passive sense not just a conversion.
  - Fix big with transform vector where -ve vector was returned in some cases due to intermediate quat being transformed to northern hemisphere.