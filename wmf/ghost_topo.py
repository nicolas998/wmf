from wmf import wmf 
import numpy as np 
from scipy.spatial import Voronoi, voronoi_plot_2d
from skimage.morphology import dilation, square
import os
import osgeo

class ghost_preprocess():
    
    def __init__(self, watershed, path_dem, seg_threshold = 400, seg_point_distance = 100,thre_sens = 10):
        '''Defines the class to derive the topology required by ghost (.riv and .mesh files)
        Parameters:
            - watershed: A watershed element obtained with wmf.SimuBasin            
            - seg_threshold: minimum distance to divide channels into segments.
            - seg_point_distance: the distance of the perpendicular points to the channel segments
            - thre_sens: in meters, the sensitivity to transform from channels to segments
        Returns:
            - self.wat: a copy of the watershed element
            - self.x and self.y: the X and Y cooridnates of the watershed element
            - self.links and self.links2: element that identifies where channels are'''
        self.threshold = seg_threshold
        self.seg_point_distance = seg_point_distance
        self.wat = watershed
        #Get basic geomorphology 
        self.wat.GetGeo_Cell_Basics()
        #self.wat.GetGeo_StreamOrder()
        self.wat.GetGeo_StreamOrder(threshold=watershed.threshold)
        #Get the coordinates of each element and the links only 
        self.x, self.y = wmf.cu.basin_coordxy(self.wat.structure, self.wat.ncells)
        self.links = self.wat.CellCauce * self.wat.hills_own
        channels = np.zeros(watershed.ncells)
        channels[self.wat.CellAcum>self.wat.threshold-thre_sens]=1
        self.links2 = channels * watershed.hills_own
        #Read the DEM map of the region 
        self.DEM, self.dem_prop, self.epsg = wmf.read_map_raster(path_dem)
    
    def get_segments_topology(self):
        '''Using channel2segment obtains the topological connection 
        of the segments.
        Parameters: 
            - threshold: the minium req lenght (m) to obtain a segment.
        Returns:
            - self.river_topology: list of list describing the connections among segments.
            - self.river_centers: list with the centers size: prop -1
            - self.river_length: list with the length size: prop -1'''
        #Code starts
        start = 0
        prop = [[0,-999,self.x[-1],self.y[-1],-999,-999,self.wat.CellHorton_Hill[-1],self.wat.ncells]]
        new_dest = [0]
        for c, dest in enumerate(self.wat.hills[1][::-1]):
            d = new_dest[dest]
            p, x1, y2, start = self.__channel2segments__(c+1, start, d)
            prop.extend(p)
            new_dest.append(start)
        self.river_topology = prop
        self.__get_segments_center_length__()
        self.__get_segment_sinuosity__()
    
    def get_mesh_river_points(self, dist = 100, clean_close_points = True, 
                              min_river2river_distance = 50):
        '''Obtains the two centers of the lines that are perpendicular 
        to each river segment'''
        if min_river2river_distance > dist:
            print('Warning: river to river point distance is greater than points distance to river\
                  the program will set it equal to dist*1.5')
            min_river2river_distance = dist*0.5
        Xp = []
        Yp = []
        for l in range(1,len(self.river_topology)):
            #Get the coordinates
            xo = self.river_topology[l][2]
            yo = self.river_topology[l][3]
            d = self.river_topology[l][1]
            xd = self.river_topology[d][2]
            yd = self.river_topology[d][3]
            #Get the centroid coordinate
            xc, yc = self.river_center[l-1]
            #Compute the slope
            if xo==xd:
                m = 0.0
            elif yo==yc:
                m = -90
            else:
                m = -1 / ((yo-yd)/(xo-xd))
            #Compute the new X and Y
            x1 = xc - self.seg_point_distance/(np.sqrt(1+m**2))
            y1 = m*(x1-xc)+yc

            x2 = xc + self.seg_point_distance/(np.sqrt(1+m**2))
            y2 = m*(x2-xc)+yc

            Xp.append([x1,x2])
            Yp.append([y1,y2])
        Xv = np.array(Xp).T.reshape(len(Xp)*2)
        Yv = np.array(Yp).T.reshape(len(Yp)*2)
        XYr = np.vstack([Xv, Yv])
        self.mesh_points_river = XYr
        if clean_close_points:
            self.__clean_river_points__(min_river2river_distance)
    
    def get_mesh_grid_points(self, mesh_spaces = 8, border_iter = 3,clean_with_river = True, 
                            min_dem2river_distance = 100):
        '''Obtains the X and Y coordinates for the mesh and the borders of the mesh.
        Parameters:
            - mesh_spaces: step to take valuesin the grid given by the DEM.
            - border_iter: number of times to perform the dilation that generates the 
                border points
        Returns:
            - XYm: np.array (2,Nmesh) with the coordinates of the points inside the mesh.
            - XYb: np.array(2,Nborder) with the coordinates of the points in the border'''
        # Get the X and Y values fom the DEM mesh
        x_vect, y_vect = wmf.cu.basin_coordxy(self.wat.structure, self.wat.ncells)
        x_mask = wmf.cu.basin_float_var2map(self.wat.structure, x_vect, wmf.cu.ncols,wmf.cu.nrows, self.wat.ncells)
        y_mask = wmf.cu.basin_float_var2map(self.wat.structure, y_vect, wmf.cu.ncols,wmf.cu.nrows, self.wat.ncells)

        #Obtain a set of points spaced in the grid
        x_steps = np.arange(0,x_mask.shape[0],mesh_spaces)
        y_steps = np.arange(0,x_mask.shape[1],mesh_spaces)
        xv,yv = np.meshgrid(x_steps, y_steps)

        # Get the xy points inside the 
        XYm = []
        for i in x_steps:
            for j in y_steps:
                if x_mask[i,j] > 0:
                    XYm.append([x_mask[i,j],y_mask[i,j]])
        XYm = np.array(XYm).T

        borders = []
        for j in [1,5]:
            #Increase the border n times
            new = np.copy(x_mask)
            new[new > 0] = 1    
            for i in range(border_iter*j):
                old = np.copy(new)
                new = dilation(old, square(3))
            border = new - old
            border = np.where(border>0)

            #Converts the border to a list of X and Y points 
            x_border = []
            y_border = []
            for col in border[0]:
                x_border.append(wmf.cu.dxp*(col - 0.5) + wmf.cu.xll)
            for row in border[1]:
                y_border.append(wmf.cu.dxp*(wmf.cu.nrows - row + 0.5) + wmf.cu.yll)
            borders.append(np.vstack([x_border, y_border]))
        self.mesh_points_dem = XYm 
        self.mesh_points_boundary = borders
        if clean_with_river:
            self.__clean_mesh_points__(min_dem2river_distance)
    
    def get_voronoi_polygons(self):
        # Get the array with all the points for the voronoi
        XYall = np.hstack([self.mesh_points_river,self.mesh_points_dem,self.mesh_points_boundary[0]]).T
        categories = np.hstack([np.ones(self.mesh_points_river.shape[1]),
                                np.ones(self.mesh_points_dem.shape[1])*2,
                               np.ones(self.mesh_points_boundary[0].shape[1])*3])
        self.mesh_points_all = XYall
        self.vor = Voronoi(XYall)
        self.vor_cat = categories
    
    def define_polygons_topology(self, define_left_right = True):
        poly_prop = []
        new_numbers = []
        old_numbers = []
        cont = 1
        for poly in range(self.vor.points.shape[0]):
            old_numbers.append(poly)
            if self.vor_cat[poly] < 3:
                poly_prop.append(self.get_polygon_prop(poly,False))
                new_numbers.append(cont)        
                cont+=1
            else:
                new_numbers.append(-9)
        xyp = []
        for p in poly_prop:
            xyp.append(p[0])
        self.polygons_topology = poly_prop 
        self.polygons_xy = np.array(xyp)
        # Defiunes the left and right of the river topo
        if define_left_right:
            self.__get_left_right__(self.polygons_xy, self.river_center)
    
    def get_polygon_prop(self, elem, plot = False):    
        region = self.vor.regions[self.vor.point_region[elem]]
        polygon = [self.vor.vertices[i] for i in region]
        cent = self.vor.points[elem]        
        Z = self.__get_z_from_dem__(cent[0],cent[1])
        area = self.__polygon_area__(np.array(polygon).T[0],np.array(polygon).T[1])
        if plot:
            if self.vor_cat[elem] < 3:
                pl.fill(*zip(*polygon), zorder = 1, alpha = 0.5)
            else:
                pl.fill(*zip(*polygon), zorder = 1, alpha = 0.5, c = 'k')
            pl.scatter(cent[0],cent[1], c = 'k', zorder = 0)

        list1_as_set = set(region)
        n_points = self.vor.point_region.shape[0]
        neighbors = []
        is_border = []
        lface = []
        dneigh = []
        for r in range(n_points):
            region2 = self.vor.regions[self.vor.point_region[r]]
            intersection = list1_as_set.intersection(region2)
            intersection_as_list = list(intersection)
            if len(intersection_as_list) > 1 and len(intersection_as_list)<3:
                if self.vor_cat[r] < 3:
                    is_border.append(1)
                else:
                    is_border.append(0)
                neighbors.append(r)
                v1 = np.array(self.vor.vertices[intersection_as_list][0])
                v2 = np.array(self.vor.vertices[intersection_as_list][1])
                c1 = self.vor.points[r]
                lface.append(np.linalg.norm(v1- v2, ord = 2, axis=0))
                dneigh.append(np.linalg.norm(c1- cent, ord = 2, axis=0))
                if plot:
                    pl.scatter(c1[0], c1[1],c = 'r')

        if plot:   
            for i in neighbors:
                region2 = self.vor.regions[self.vor.point_region[i]]
                polygon2 = [self.vor.vertices[i] for i in region2]
                if self.vor_cat[i] < 3:
                    pl.fill(*zip(*polygon2), c = 'g', alpha = 0.5)
                else:
                    pl.fill(*zip(*polygon2), c = 'r', alpha = 0.5)
        return cent, Z, area, len(lface), neighbors,lface, dneigh, is_border, polygon
    
    def write_mesh_file(self, path, shp_path = None):
        f = open(path,'w')
        n_points = len(self.polygons_topology)
        f.write('NUMELE\t%d\n' % n_points)
        f.write('INDEX   X   Y   Zmin    Zmax    Area    nFaces\n')
        for c, p in enumerate(self.polygons_topology):
            f.write('%d\t%.2f\t%.2f\t%.2f\t%.2f\t%.2f\t%d\n' % (c+1,p[0][0],p[0][1],p[1]-20,p[1],p[2],p[3]))
        f.write('\n')
        f.write('INDEX   ID_Neighbors    Lenght_Face_Neighbors   Distance_to_Neighbors\n')
        for c, p in enumerate(self.polygons_topology):
            f.write('%d ' % (c+1))
            neighbors = (np.array(p[4])+1)*np.array(p[-2])
            for n in neighbors:
                f.write('%d ' % n)
            for lf in p[5]:
                f.write('%.2f ' % lf)
            for lf in p[6]:
                f.write('%.2f ' % lf)
            f.write('\n') 
        f.close()
        if shp_path is not None:
            self.__write_mesh_shp__(shp_path)
        
    def write_river_file(self, path, shp_path = None):
        prop = self.river_topology
        f = open(path, 'w')
        f.write('NUMRIV\t%d\n' % len(prop[1:]))
        f.write('INDEX	X	Y	ZMIN	ZMAX	LENGTH	DOWN	LEFT	RIGHT	SHAPE	MATRL	BC	RES	XAREA	INACT	LAKE	LRIV\n')
        prop[1][1] = -4
        for seg,cen,lr,lriv in zip(prop[1:],self.river_center,self.river_left_right,self.river_sinuosity):
            f.write('%d\t%.2f\t%.2f\t%.2f\t%.2f\t%d\t' % (seg[0], cen[0], cen[1], seg[4],seg[5],seg[1]))
            f.write('%d\t%d\t%d\t%d\t' % (lr[0],lr[1], seg[-2], seg[-2]))
            for i in range(5):
                f.write('0\t')
            f.write('%.2f\n' % lriv)
        f.close()
        if shp_path is not None:
            self.__write_river_shp__(shp_path)
            
    def __write_river_shp__(self, path):
        prop = self.river_topology
        DriverFormat='ESRI Shapefile'    
        sr = osgeo.osr.SpatialReference()
        sr.ImportFromEPSG(int(self.wat.epsg))
        driver = osgeo.ogr.GetDriverByName(DriverFormat)
        if os.path.exists(path):
            driver.DeleteDataSource(path)
        shapeData = driver.CreateDataSource(path)
        layer = shapeData.CreateLayer('layer1', sr, osgeo.ogr.wkbLineString)
        layerDefinition = layer.GetLayerDefn()
        names = ['segment','long[m]','z[m]','down','left','right','horder']
        fmts = [osgeo.ogr.OFTInteger,osgeo.ogr.OFTReal,osgeo.ogr.OFTReal,
               osgeo.ogr.OFTInteger, osgeo.ogr.OFTInteger, osgeo.ogr.OFTInteger,
               osgeo.ogr.OFTInteger]
        for name,fmt in zip(names, fmts):
            new_field=osgeo.ogr.FieldDefn(name,fmt)
            layer.CreateField(new_field)
        featureFID = 0
        prop[1][1] = 0
        for l in range(1,len(self.river_topology)):
            xo = prop[l][2]
            yo = prop[l][3]
            d = prop[l][1]
            xd = prop[d][2]
            yd = prop[d][3]
            line = osgeo.ogr.Geometry(osgeo.ogr.wkbLineString)
            line.AddPoint_2D(float(xo),float(yo))
            line.AddPoint_2D(float(xd),float(yd))
            feature = osgeo.ogr.Feature(layerDefinition)
            feature.SetGeometry(line)
            feature.SetFID(0)
            feature.SetField('segment',int(prop[l][0]))
            feature.SetField('long[m]',float(self.river_length[l-1]))
            feature.SetField('z[m]',float(prop[l-1][5]))
            feature.SetField('down',int(d))
            feature.SetField('left',int(self.river_left_right[l-1][0]))
            feature.SetField('right',int(self.river_left_right[l-1][1]))
            feature.SetField('horder',int(prop[l][-2]))
            layer.CreateFeature(feature)
            #line.Destroy()
            #feature.Destroy()
        shapeData.Destroy()
    
    def __write_mesh_shp__(self, path):
        DriverFormat='ESRI Shapefile'    
        sr = osgeo.osr.SpatialReference()
        sr.ImportFromEPSG(int(self.wat.epsg))
        driver = osgeo.ogr.GetDriverByName(DriverFormat)
        if os.path.exists(path):
            driver.DeleteDataSource(path)
        shapeData = driver.CreateDataSource(path)
        layer = shapeData.CreateLayer('layer1', sr, osgeo.ogr.wkbPolygon)
        layerDefinition = layer.GetLayerDefn()
        names = ['polygon','area[m2]','z[m]','nfaces']
        fmts = [osgeo.ogr.OFTInteger,osgeo.ogr.OFTReal,osgeo.ogr.OFTReal,
              osgeo.ogr.OFTInteger]
        for name,fmt in zip(names, fmts):
            new_field=osgeo.ogr.FieldDefn(name,fmt)
            layer.CreateField(new_field)
        featureFID = 0
        for c, p in enumerate(self.polygons_topology):
            ring = osgeo.ogr.Geometry(osgeo.ogr.wkbLinearRing)
            for i in np.array(p[-1]):
                ring.AddPoint(x=float(i[0]), y = float(i[1]))
            poly=osgeo.ogr.Geometry(osgeo.ogr.wkbPolygon)
            poly.AddGeometry(ring)
            feature = osgeo.ogr.Feature(layerDefinition)
            feature.SetGeometry(poly)
            feature.SetFID(0)
            feature.SetField('polygon',int(c+1))
            feature.SetField('area[m2]',float(p[2]))
            feature.SetField('z[m]',float(p[1]))
            feature.SetField('nfaces',int(p[3]))
            layer.CreateFeature(feature)
        shapeData.Destroy()
    
    def __get_left_right__(self, poly_xy, centers):
        left_right = []
        for c in centers:
            dist = np.linalg.norm(poly_xy - c, ord = 2, axis=1)  
            pos = []
            while len(pos)<2:
                p = np.argmin(dist)
                pos.append(p+1)
                dist[p] = 9999
            left_right.append(pos)
        self.river_left_right = left_right
    
    def __polygon_area__(self, x,y):
        correction = x[-1] * y[0] - y[-1]* x[0]
        main_area = np.dot(x[:-1], y[1:]) - np.dot(y[:-1], x[1:])
        return 0.5*np.abs(main_area + correction)

    def __get_z_from_dem__(self, x,y):
        col = np.floor((x - self.dem_prop[2])/self.dem_prop[-3]) -1 
        row = self.dem_prop[1] - np.floor((y-self.dem_prop[3])/self.dem_prop[-2])+1
        return np.percentile(self.DEM[int(col)-3:int(col)+3, int(row)-3:int(row)+3], 50)
    
    def __clean_river_points__(self, min_dist = 250):
        h_orders = np.array(self.river_topology)[:,-2]
        max_h = h_orders.max()
        drop_list = []
        excluded_pos = []
        for order in np.arange(max_h, 0, -1):
            pos = np.where(h_orders == order)[0]
            if order == max_h:
                pos = pos[1:]
            for c,i in enumerate(self.mesh_points_river.T[pos]):
                excluded_pos.append(pos[c])
                dist = np.linalg.norm(self.mesh_points_river.T - i, ord = 2, axis=1)    
                for c2,d in enumerate(dist):
                    if d>0 and d<min_dist and c2 not in excluded_pos:
                        drop_list.append(c2)
                        excluded_pos.append(c2)
        self.mesh_points_river = np.delete(self.mesh_points_river.T, drop_list, axis = 0).T
    
    def __clean_mesh_points__(self, min_dist = 100):        
        pos = []
        for i in self.mesh_points_river.T:
            dist = np.linalg.norm(self.mesh_points_dem.T - i, ord = 2, axis=1)    
            if np.min(dist) == 0:
                dist[dist == 0] = 9999
            if np.min(dist) < min_dist:
                pos.append(np.argmin(dist))
        self.mesh_points_dem = np.vstack([np.delete(self.mesh_points_dem[0], pos),np.delete(self.mesh_points_dem[1], pos)])
    
    def __get_segments_center_length__(self):
        '''Obtains the X,Y centroid and the straight length of each 
        segment.
        Parameters:
            - prop: list of list with the topology of the segments.
        Returns:
            - self.river_centers: list with the centers size: prop -1
            - self.river_length: list with the length size: prop -1'''
        centers = []
        lenght = []
        for l in range(1,len(self.river_topology)):
            xo = self.river_topology[l][2]
            yo = self.river_topology[l][3]
            d = self.river_topology[l][1]
            xd = self.river_topology[d][2]
            yd = self.river_topology[d][3]
            xc = (xo+xd)/2.
            yc = (yo+yd)/2.
            centers.append([xc,yc])
            lenght.append(np.sqrt((xo-xd)**2 + (yo-yd)**2))
        self.river_center = centers
        self.river_length = lenght
        
    def __get_segment_sinuosity__(self):
        '''Obtains the sinuosity of the segments using the straight length 
        end the cells directional path.
        Parameters: 
            - prop: list of list with the topology of the segments.
            - length: list with the segments length (derived from get_segments_length).
        Returns:
            - sinuosity: the sinuosity factor (Lr / Ls).
            - real_length: the lenght of the link crossing the DEM cells.'''
        sinuos = []
        real_length = []
        for p,le in zip(self.river_topology[1:], self.river_length):
            pos_o = p[-1]
            pos_d = self.river_topology[p[1]][-1]
            r_length = 0
            while pos_o != pos_d:
                r_length += self.wat.CellLong[pos_o]
                pos_o = self.wat.ncells - self.wat.structure[0][pos_o]
            sino = r_length / le
            if sino < 1:
                sino = 1
            sinuos.append(sino)
            real_length.append(r_length)
        self.river_sinuosity =  sinuos
        self.river_real_length = real_length
    
    def __channel2segments__(self, link, start = 1, dest = -4):
        '''Converts a channel link to a set of N segments 
        Parameters: 
            - link: the link to process.
            - start: the initial count for the segments.
            - dest: the downstream segment.            
        Returns:
            -segment list: id, dest, x_start, y_start, z_min, z_max, order, cell_start'''
        #Get the hydrological segment
        pos = np.where(self.links == link)
        if pos[0].size > 0:
            stream = self.wat.CellLong[pos]
        else:
            pos = np.where(self.links2 == link)
            stream = self.wat.CellLong[pos]

        #Obtains the segments of the channel 
        stream_cat = np.ceil(stream.cumsum() / self.threshold)
        last = stream_cat[-1]
        stream_cat = last+1-stream_cat

        #Border correction of small channels 
        if stream_cat.size > 1:
            if stream_cat[stream_cat == stream_cat[-1]].size == 1:
                stream_cat[-1] = stream_cat[-2]
                stream_cat = stream_cat - 1 
            if stream_cat[stream_cat == stream_cat[0]].size == 1:
                stream_cat[0] = stream_cat[1]

        #Set the properties of the segment
        xt = self.x[pos]
        yt = self.y[pos]
        Z = self.wat.CellHeight[pos]
        H = self.wat.CellHorton_Stream[pos]

        properties = []

        for c, i in enumerate(np.unique(stream_cat)):
            pos2 = np.where(stream_cat == i)[0]
            prop = []
            prop = [int(i+start)]        
            if i > stream_cat[-1]:
                prop.append(int(i-1+start))
            else:
                prop.append(int(dest))
            #SEt the x, y coordinates of the link start
            prop.append(xt[pos2][0])
            prop.append(yt[pos2][0])
            #Get the height of the link 
            prop.append(Z[pos2].mean() - 20)
            prop.append(Z[pos2].mean())
            #Get the order 
            prop.append(int(np.max(H[pos2])))
            #Get the cell position of the start of the segment 
            prop.append(pos[0][pos2][0])
            properties.append(prop)
        return properties, xt[pos2][0], yt[pos2][0], int(i+start)
