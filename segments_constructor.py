from shapely.geometry import LineString, Point, MultiLineString
import geopandas as gpd
import os
import math
from tqdm import tqdm
import time
from get_data_functions import get_data

class SegmentConstructor:
    def __init__(self, classified_profiles, output_folder, route_number):
        self.classified_profiles = classified_profiles
        self.current_crs = classified_profiles.crs
        self.current_bounds = tuple(classified_profiles.total_bounds)
        self.output_folder = output_folder
        self.route_number = route_number
        self.filter_route = f"numero='{route_number}'"
        self.route = get_data(self.filter_route, "BDTOPO_V3:route_numerotee_ou_nommee", self.current_bounds)
        self.filter_PR = f"route='{route_number}'"
        self.PR_route = get_data(self.filter_PR, "BDTOPO_V3:point_de_repere", self.current_bounds)
        
        # Créer un index spatial pour accélérer la recherche de points
        print("Création de l'index spatial...")
        self.spatial_index = self.classified_profiles.sindex
        print("Index spatial créé")

    def calculate_distance(self, point1, point2):
        """Calculate the distance between two points"""
        return math.sqrt((point2.x - point1.x)**2 + (point2.y - point1.y)**2)
    
    def determine_closest_point(self, given_point):
        """Trouve le point le plus proche avec une recherche optimisée."""
        # Utiliser l'index spatial pour trouver rapidement les candidats les plus proches
        try:
            # Créer un buffer autour du point pour la recherche
            buffer_dist = 5  # métres
            buffered_bounds = (
                given_point.x - buffer_dist,
                given_point.y - buffer_dist,
                given_point.x + buffer_dist,
                given_point.y + buffer_dist
            )
            
            # Trouver les points dans ce buffer
            possible_matches_index = list(self.spatial_index.intersection(buffered_bounds))
            
            if not possible_matches_index:
                return None, float('inf')
                
            possible_matches = self.classified_profiles.iloc[possible_matches_index]
            
            # Parmi ces candidats, trouver le plus proche
            closest_row = None
            min_distance = float('inf')
            
            for index, row in possible_matches.iterrows():
                distance = self.calculate_distance(row.geometry, given_point)
                if distance < min_distance:
                    min_distance = distance
                    closest_row = row
                    
            return closest_row, min_distance
            
        except Exception as e:
            print(f"Erreur lors de la recherche du point le plus proche: {e}")
            return None, float('inf')
        
    def is_convertible_to_int(self, x):
        try:
            int(str(x))
            return True
        except ValueError:
            return False

    def find_closest_PR(self, point, PR_current):
        """
        Find the closest PR point with the smallest PR number
        """
        # Utiliser l'index spatial pour trouver rapidement les candidats les plus proches
        spatial_index_PR = PR_current.sindex
        try:
            # Créer un buffer autour du point pour la recherche
            buffer_dist = 1500  # métres
            buffered_bounds = (
                point.x - buffer_dist,
                point.y - buffer_dist,
                point.x + buffer_dist,
                point.y + buffer_dist
            )
            
            # Trouver les points dans ce buffer
            possible_matches_index = list(spatial_index_PR.intersection(buffered_bounds))
            print(f"Nombre de points de repère trouvés: {len(possible_matches_index)}")
            
            if not possible_matches_index:
                return None
                
            possible_matches = PR_current.iloc[possible_matches_index]

            # Filter possible_matches to keep only rows where 'numero' can be converted to an integer
            possible_matches = possible_matches[possible_matches['numero'].apply(self.is_convertible_to_int)]
            print(f"Points de repère restants après filtrage: {len(possible_matches)}")

            if possible_matches.empty:
                return None

            # Chercher les 2 PR les plus proches du point
            closest_two = possible_matches.distance(point).nsmallest(2).index
            possible_matches = possible_matches.loc[closest_two]

            print(f"Nombre dans possible matches: {len(possible_matches)}")

            # Parmi ces candidats, trouver le minimal
            candidates = []
            for index, row in possible_matches.iterrows():
                try:
                    # Extract PR number
                    pr_number = int(row['numero'])
                    candidates.append((row, pr_number))
                except (IndexError, ValueError) as e:
                    print(f"Error extracting PR number")
                    continue
        
            if not candidates:
                return None
            else:
                # Sort candidates by PR number
                candidates.sort(key=lambda x: x[1])
                minimal_PR = candidates[0][0]
                
                return minimal_PR
            
        except Exception as e:
            print(f"Erreur lors de la recherche du PR de référence: {e}")
            return None

    def construct_segments(self):
        all_ouvrages = []
        start_time = time.time()

        print("Début de construct_segments()")
        print(f"Nombre de points dans classified_profiles: {len(self.classified_profiles)}")
        print(f"CRS de la route: {self.route.crs}")
        print(f"CRS des profils: {self.classified_profiles.crs}")

        if len(self.classified_profiles) == 0:
            print("classified_profiles est vide. Aucun segment ne sera généré.")
            return gpd.GeoDataFrame(columns=["geometry", "startpoint", "endpoint", "length", "classification"], crs=self.current_crs)

        for index, geom in enumerate(self.route.geometry):
            if geom.geom_type == "MultiLineString":
                for line_idx, line in enumerate(geom.geoms):
                    if isinstance(line, LineString):
                        i = 0
                        # Utiliser simplement la longueur de la ligne au lieu du calcul géodésique
                        length_line = line.length
                        print(f"Traitement de la ligne {index+1}.{line_idx+1} - Longueur: {length_line:.2f} m")

                        line_buffer = line.buffer(1)  # Create 1-meter buffer around the line
                        PR_current = self.PR_route[self.PR_route.geometry.intersects(line_buffer)]
                        print(f"Nombre de points de repère dans la ligne {index+1}.{line_idx+1}: {len(PR_current)}")

                        with tqdm(total=int(length_line), desc=f"Processing Line {index+1}.{line_idx+1}") as pbar:
                            while i < length_line:
                                if time.time() - start_time > 3600:  # Timeout après 1 heure
                                    print("Timeout atteint. Arrêt du traitement.")
                                    break
                                    
                                pointi_geo = line.interpolate(i)
                                
                                # Log périodique
                                if i % 100 == 0:
                                    print(f"Position actuelle: {i:.2f}/{length_line:.2f} m")
                                
                                closest_row, min_distance = self.determine_closest_point(pointi_geo)
                                
                                if closest_row is None:
                                    i += 1
                                    pbar.update(1)
                                    continue

                                if min_distance < 5:
                                    print(f"Point proche trouvé à {i:.2f} m - Distance: {min_distance:.2f} m")

                                    profile_type = closest_row['classification']

                                    list_points = [pointi_geo]
                                    j = i + 1
                                    max_search = min(i + 1000, length_line)  # Limiter la recherche pour éviter les boucles infinies
                                    
                                    # Collecter les points pour créer un segment avec limite d'itérations
                                    iteration_count = 0
                                    max_iterations = 1000

                                    hauteur_max = 0
                                    pente_max = 0
                                    hauteurs = []
                                    pentes = []
                                    
                                    while j < max_search and iteration_count < max_iterations:
                                        pointj_geo = line.interpolate(j)
                                        closest_row_j, min_distance_j = self.determine_closest_point(pointj_geo)
                                        
                                        if closest_row_j is None or min_distance_j > 1.5:
                                            break
                                        
                                        # Vérifier que le type de profil est le même pour continuer le segment
                                        if closest_row_j['classification'] != profile_type:
                                            i += 1
                                            pbar.update(1)
                                            break

                                        hauteur = closest_row_j['max_height_difference']
                                        hauteurs.append(hauteur)
                                        hauteur_max = max(hauteur_max, hauteur)

                                        if closest_row_j['slope_ouvrage_section'] is not None:
                                            pente = closest_row_j['slope_ouvrage_section']
                                        else:
                                            pente = closest_row_j['slope_ouvrage_total']
                                        pentes.append(pente)
                                        pente_max = max(pente_max, pente)

                                        list_points.append(pointj_geo)
                                        j += 1
                                        iteration_count += 1
                                    
                                    hauteur_max = max(hauteurs) if hauteurs else 0
                                    hauteur_moyenne = sum(hauteurs) / len(hauteurs) if hauteurs else 0
                                    pente_max = max(pentes) if pentes else 0
                                    pente_moyenne = sum(pentes) / len(pentes) if pentes else 0

                                    # Si on a interrompu à cause du max d'itérations
                                    if iteration_count >= max_iterations:
                                        print(f"Arrêt après {max_iterations} itérations à la distance {i}")
                                    
                                    # Vérifier qu'il y a au moins 2 points avant de créer la LineString
                                    if len(list_points) >= 2:
                                        try:
                                            segment = LineString(list_points)

                                            segment_startpoint = segment.interpolate(0)
                                            segment_endpoint = segment.interpolate(-1)

                                            PR_start = self.find_closest_PR(segment_startpoint, PR_current)
                                            PR_end = self.find_closest_PR(segment_endpoint, PR_current)

                                            closest_point_on_line_PR_start = line.interpolate(line.project(PR_start.geometry))
                                            closest_point_on_line_PR_end = line.interpolate(line.project(PR_end.geometry))

                                            abcisse_start = line.project(segment_startpoint) - line.project(closest_point_on_line_PR_start)
                                            abcisse_end = line.project(segment_endpoint) - line.project(closest_point_on_line_PR_end)

                                            segment_name = f"{self.route_number}_PR{PR_start['numero']}-{int(round(abcisse_start, -1))}_{PR_start['cote']}"
                                            
                                            all_ouvrages.append({
                                                'geometry': segment,
                                                'length': j - i,
                                                'classification': profile_type,
                                                'hauteur_max': hauteur_max,
                                                'pente_max': pente_max,
                                                'hauteur_moyenne': hauteur_moyenne,
                                                'pente_moyenne': pente_moyenne,
                                                'PR_start': PR_start['libelle'],
                                                'PR_end': PR_end['libelle'],
                                                'abcisse_start': round(abcisse_start, -1),
                                                'abcisse_end': round(abcisse_end, -1),
                                                'nom': segment_name,
                                                'route': self.route_number
                                            })
                                            
                                            delta = j - i
                                            pbar.update(delta)
                                            i = j
                                            print(f"Segment créé: {delta:.2f} m, Type: {profile_type}")
                                        except Exception as e:
                                            print(f"Erreur lors de la création du segment: {e}")
                                            i += 1
                                            pbar.update(1)
                                    else:
                                        print(f"Pas assez de points pour créer un segment à la distance {i}")
                                        i += 1
                                        pbar.update(1)
                                else:
                                    i += 1
                                    pbar.update(1)
                    else:
                        print(f"Géométrie à l'index {index} n'est pas une LineString, mais {type(line)}")
            else:
                print(f"Géométrie à l'index {index} n'est pas une MultiLineString, mais {geom.geom_type}")

        if not all_ouvrages:
            print("all_ouvrages est vide après traitement.")
            return gpd.GeoDataFrame(columns=["geometry", "startpoint", "endpoint", "length", "classification", "hauteur_max", "pente_max"], crs=self.current_crs)

        print(f"Segments générés: {len(all_ouvrages)}")

        ouvrages_gdf = gpd.GeoDataFrame(all_ouvrages, crs=self.current_crs, geometry="geometry")

        return ouvrages_gdf

    def save_output(self, ouvrages_gdf):
        # Create output folder if it doesn't exist
        os.makedirs(self.output_folder, exist_ok=True)
        file_name = f"ouvrages_{self.route_number}.gpkg"
        output_file = os.path.join(self.output_folder, file_name)
        
        # Save segments
        ouvrages_gdf.to_file(output_file, driver='GPKG', layer='segments')
        
        print(f"Ouvrage segments saved as: {output_file}")