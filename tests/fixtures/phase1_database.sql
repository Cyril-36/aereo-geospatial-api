-- SQLite dump of a database written by the Phase 1 application (commit b0db587):
-- three EXTRACTED uploads (UTM Shapefile, KML, Shapefile without .prj) and one FAILED.
PRAGMA foreign_keys=OFF;
BEGIN TRANSACTION;
CREATE TABLE files (
	id VARCHAR(36) NOT NULL, 
	original_filename VARCHAR(255) NOT NULL, 
	format VARCHAR(16) NOT NULL, 
	storage_path TEXT NOT NULL, 
	size_bytes INTEGER NOT NULL, 
	sha256 VARCHAR(64) NOT NULL, 
	status VARCHAR(16) NOT NULL, 
	crs_status VARCHAR(16), 
	crs TEXT, 
	crs_wkt TEXT, 
	crs_origin VARCHAR(32), 
	feature_count INTEGER, 
	warnings JSON NOT NULL, 
	error_code VARCHAR(64), 
	error_message TEXT, 
	created_at DATETIME NOT NULL, 
	processed_at DATETIME, 
	PRIMARY KEY (id)
);
INSERT INTO files VALUES('0f9cf5dd-7760-41bf-9017-5d74ea768ece','survey_utm.zip','SHAPEFILE','data/uploads/0f9cf5dd-7760-41bf-9017-5d74ea768ece.zip',2225,'92047eba6c064e3f419b0b5cb47356b7b9defee80605bfa914f7ef33eeee9682','EXTRACTED','KNOWN','EPSG:32643','PROJCS["WGS_1984_UTM_Zone_43N",GEOGCS["GCS_WGS_1984",DATUM["D_WGS_1984",SPHEROID["WGS_1984",6378137.0,298.257223563]],PRIMEM["Greenwich",0.0],UNIT["Degree",0.0174532925199433]],PROJECTION["Transverse_Mercator"],PARAMETER["False_Easting",500000.0],PARAMETER["False_Northing",0.0],PARAMETER["Central_Meridian",75.0],PARAMETER["Scale_Factor",0.9996],PARAMETER["Latitude_Of_Origin",0.0],UNIT["Meter",1.0]]','PRJ_FILE',2,'[]',NULL,NULL,'2026-10-08 10:07:31.526873','2026-10-08 10:07:31.566107');
INSERT INTO files VALUES('17f40b47-36fe-4886-9fcf-689c576054b2','field.kml','KML','data/uploads/17f40b47-36fe-4886-9fcf-689c576054b2.kml',829,'0c0014c698a5771a28a899d77288c92eb4a4fefefb098154b83d794d608b1c80','EXTRACTED','KNOWN','EPSG:4326','GEOGCRS["WGS 84",ENSEMBLE["World Geodetic System 1984 ensemble",MEMBER["World Geodetic System 1984 (Transit)"],MEMBER["World Geodetic System 1984 (G730)"],MEMBER["World Geodetic System 1984 (G873)"],MEMBER["World Geodetic System 1984 (G1150)"],MEMBER["World Geodetic System 1984 (G1674)"],MEMBER["World Geodetic System 1984 (G1762)"],MEMBER["World Geodetic System 1984 (G2139)"],MEMBER["World Geodetic System 1984 (G2296)"],ELLIPSOID["WGS 84",6378137,298.257223563,LENGTHUNIT["metre",1]],ENSEMBLEACCURACY[2.0]],PRIMEM["Greenwich",0,ANGLEUNIT["degree",0.0174532925199433]],CS[ellipsoidal,2],AXIS["geodetic latitude (Lat)",north,ORDER[1],ANGLEUNIT["degree",0.0174532925199433]],AXIS["geodetic longitude (Lon)",east,ORDER[2],ANGLEUNIT["degree",0.0174532925199433]],USAGE[SCOPE["Horizontal component of 3D system."],AREA["World."],BBOX[-90,-180,90,180]],ID["EPSG",4326]]','KML_SPECIFICATION',3,'[]',NULL,NULL,'2026-10-08 10:07:31.569964','2026-10-08 10:07:31.575295');
INSERT INTO files VALUES('916236fc-2799-4084-9584-7858c5ea0965','survey_noprj.zip','SHAPEFILE','data/uploads/916236fc-2799-4084-9584-7858c5ea0965.zip',1660,'8ac3b1d5ee24f89c0e6b51b98448551d79075d6d33573c3cac3f0cb2850e6955','EXTRACTED','UNKNOWN',NULL,NULL,'NONE',2,'[{"code": "CRS_MISSING", "message": "No .prj file: the source CRS is unknown."}]',NULL,NULL,'2026-10-08 10:07:31.578171','2026-10-08 10:07:31.580943');
INSERT INTO files VALUES('5eacb5f2-9b9d-475b-99db-acfa34d4ffde','broken.zip','SHAPEFILE','data/uploads/5eacb5f2-9b9d-475b-99db-acfa34d4ffde.zip',9,'208b1a8635a2c8e7d1aa086ef245d276a980c79e77612b13b7a495bb62bf65d6','FAILED',NULL,NULL,NULL,NULL,NULL,'[]','INVALID_ZIP','The .zip file is not a valid ZIP archive.','2026-10-08 10:07:31.583830','2026-10-08 10:07:31.584353');
CREATE TABLE features (
	id INTEGER NOT NULL, 
	file_id VARCHAR(36) NOT NULL, 
	"index" INTEGER NOT NULL, 
	source_id TEXT, 
	geometry_type VARCHAR(32), 
	source_geometry JSON, 
	source_crs TEXT, 
	properties JSON NOT NULL, 
	folder_path JSON NOT NULL, 
	issue_code VARCHAR(64), 
	issue_detail TEXT, 
	warnings JSON NOT NULL, 
	PRIMARY KEY (id), 
	UNIQUE (file_id, "index"), 
	FOREIGN KEY(file_id) REFERENCES files (id) ON DELETE CASCADE
);
INSERT INTO features VALUES(1,'0f9cf5dd-7760-41bf-9017-5d74ea768ece',0,'0','Polygon','{"coordinates": [[[500000.0, 1434000.0], [500000.0, 1435000.0], [501000.0, 1435000.0], [501000.0, 1434000.0], [500000.0, 1434000.0]]], "type": "Polygon"}','EPSG:32643','{"plot_id": "P-17", "owner": "Rao", "survey_no": 17, "crop": "Ragi"}','[]',NULL,NULL,'[]');
INSERT INTO features VALUES(2,'0f9cf5dd-7760-41bf-9017-5d74ea768ece',1,'1','Polygon','{"coordinates": [[[502000.0, 1434000.0], [502000.0, 1434500.0], [502500.0, 1434500.0], [502500.0, 1434000.0], [502000.0, 1434000.0]], [[502100.0, 1434100.0], [502200.0, 1434100.0], [502200.0, 1434200.0], [502100.0, 1434200.0], [502100.0, 1434100.0]]], "type": "Polygon"}','EPSG:32643','{"plot_id": "P-18", "owner": "Iyer", "survey_no": 18, "crop": "Paddy"}','[]',NULL,NULL,'[]');
INSERT INTO features VALUES(3,'17f40b47-36fe-4886-9fcf-689c576054b2',0,'farm-1','Polygon','{"type": "Polygon", "coordinates": [[[77.59, 12.97, 920.0], [77.6, 12.97, 921.0], [77.6, 12.98, 922.0], [77.59, 12.98, 920.0], [77.59, 12.97, 920.0]]]}','EPSG:4326','{"name": "Farm 1", "owner": "Rao", "survey_no": "17"}','["Field visit", "Ward 12"]',NULL,NULL,'[]');
INSERT INTO features VALUES(4,'17f40b47-36fe-4886-9fcf-689c576054b2',1,'canal','LineString','{"type": "LineString", "coordinates": [[77.58, 12.96], [77.61, 12.99]]}','EPSG:4326','{"name": "Canal"}','["Field visit", "Ward 12"]',NULL,NULL,'[]');
INSERT INTO features VALUES(5,'17f40b47-36fe-4886-9fcf-689c576054b2',2,NULL,'Point','{"type": "Point", "coordinates": [77.595, 12.975]}','EPSG:4326','{"name": "Borewell"}','["Field visit", "Ward 12"]',NULL,NULL,'[]');
INSERT INTO features VALUES(6,'916236fc-2799-4084-9584-7858c5ea0965',0,'0','Polygon','{"coordinates": [[[500000.0, 1434000.0], [500000.0, 1435000.0], [501000.0, 1435000.0], [501000.0, 1434000.0], [500000.0, 1434000.0]]], "type": "Polygon"}',NULL,'{"plot_id": "P-17", "owner": "Rao", "survey_no": 17, "crop": "Ragi"}','[]',NULL,NULL,'[]');
INSERT INTO features VALUES(7,'916236fc-2799-4084-9584-7858c5ea0965',1,'1','Polygon','{"coordinates": [[[502000.0, 1434000.0], [502000.0, 1434500.0], [502500.0, 1434500.0], [502500.0, 1434000.0], [502000.0, 1434000.0]], [[502100.0, 1434100.0], [502200.0, 1434100.0], [502200.0, 1434200.0], [502100.0, 1434200.0], [502100.0, 1434100.0]]], "type": "Polygon"}',NULL,'{"plot_id": "P-18", "owner": "Iyer", "survey_no": 18, "crop": "Paddy"}','[]',NULL,NULL,'[]');
CREATE INDEX ix_files_status ON files (status);
CREATE INDEX ix_features_file_id ON features (file_id);
COMMIT;
