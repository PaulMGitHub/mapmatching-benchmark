# -*- coding: utf-8 -*-

# Copyright 2017 Google Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import datetime as dt
from operator import itemgetter
from collections import defaultdict
import requests
from itertools import groupby


import json
import folium
import numpy as np

from flask import Flask
from flask import jsonify
from flask import render_template
from flask import request


import firebase_admin
from firebase_admin import db

app = Flask(__name__)

firebase_admin.initialize_app(options={
    'databaseURL': 'https://qwiklabs-gcp-399458fe6a8ac2e0.firebaseio.com/'
})

@app.route('/', methods=['GET', 'POST'])
def index():
    return render_template('maps_draggable_directions.html')


@app.route('/generate_gps_trace', methods=['GET', 'POST'])
def generate_gps_trace():
    noise = int(request.form['noise'])
    sampling = int(request.form['sampling'])
    total = request.form['total']
    response = json.loads(request.form['response'])
    mapmatching = request.form['mapmatching']
    print("noise: {}, sampling: {}, total: {}, mapmatch: {}".format(noise, sampling, total, mapmatching))
    if mapmatching == 'true':
        overview_path = response['routes'][0]['overview_path']
        synthetic_gps_trace, trip_to_match = make_synthetic_gps_trace(overview_path, noise, sampling)
        write_folium_google_input_trip(overview_path, synthetic_gps_trace)
        render_template('google_input_latest.html')
        matched_json = matching(trip_to_match)
        return matched_json
    else:
        overview_path = response['routes'][0]['overview_path']
        synthetic_gps_trace, trip_to_match = make_synthetic_gps_trace(overview_path, noise, sampling)
        write_folium_google_input_trip(overview_path, synthetic_gps_trace)
        #mapmatch(trip_to_match)
        return render_template('google_input_latest.html')

def make_random_point(x_lng, y_lat, distance):
    # distance in meter
    r = distance / 111300
    u = np.random.uniform(0, 1)
    v = np.random.uniform(0, 1)
    w = r * np.sqrt(u)
    t = 2 * np.pi * v
    x = w * np.cos(t)
    x1 = x / np.cos(y_lat)
    y = w * np.sin(t)
    return x_lng + x1, y_lat + y, x1, y


def make_synthetic_gps_trace(path_list, noise, sampling):
    synthetic_gps_trace = []
    trip_to_match = {}
    for idx, fix in enumerate(path_list):
        if idx % sampling == 0:
            gps_lng, gps_lat, gps_lng_noise, gps_lat_noise = make_random_point(fix['lng'], fix['lat'], noise)
            gps_fix = {'location': {'latitude': gps_lat,
                                    'longitude': gps_lng,
                                    'precision': gps_lng_noise + gps_lat_noise,
                                    'speed': 50}
                       }
            synthetic_gps_trace.append(gps_fix)
    trip_to_match['google_label_path'] = [[fix['lat'], fix['lng']] for idx, fix in enumerate(path_list)]
    trip_to_match['fixes'] = synthetic_gps_trace

    return synthetic_gps_trace, trip_to_match


def write_folium_google_input_trip(path_list, synthetic_gps_trace_list):
    Map = folium.Map()

    google_input_layer = folium.FeatureGroup("Google input points")
    google_path_layer = folium.FeatureGroup("Google input path")
    synthetic_gps_layer = folium.FeatureGroup("Synthetic GPS points")

    startpoint_lat = path_list[0]['lat']
    startpoint_lon = path_list[0]['lng']

    Map.fit_bounds(bounds=[[
        startpoint_lat - 1 / 110,
        startpoint_lon - 1 / (110 * np.cos(startpoint_lat))
    ], [
        startpoint_lat + 1 / 110,
        startpoint_lon + 1 / (110 * np.cos(startpoint_lat))
    ]])

    for idx, fix in enumerate(path_list):
        google_lat = fix['lat']
        google_lon = fix['lng']
        google_popup = "Google Input Point #{}".format(idx)
        folium.CircleMarker(location=[google_lat, google_lon],
                            color='black',
                            radius=4,
                            popup=google_popup).add_to(google_input_layer)

    google_polyline = folium.PolyLine(locations=[[fix['lat'], fix['lng']] for idx, fix in enumerate(path_list)],
                                      weight=3,
                                      color='green')
    google_path_layer.add_child(google_polyline)

    for idx, fix in enumerate(synthetic_gps_trace_list):
        gps_lat = fix['location']['latitude']
        gps_lon = fix['location']['longitude']
        gps_popup = "Synthetic GPS Point #{}".format(idx)
        folium.CircleMarker(location=[gps_lat, gps_lon],
                            color='red',
                            radius=6,
                            popup=gps_popup).add_to(synthetic_gps_layer)

    Map.add_child(google_input_layer)
    Map.add_child(synthetic_gps_layer)
    Map.add_child(google_path_layer)
    Map.add_child(folium.LayerControl())

    html_title = "templates/google_input_latest.html"
    Map.save(outfile=html_title)

def matching(input):
    data = input
    map_matching = MapMatching(data)
    results = map_matching.run()
    del map_matching
    results_json = jsonify(results)
    return results_json

class MapMatching(object):
    """
        This class perform map-matching of the desired trip
    """

    def __init__(self, data):
        """
        :param data: Body of the input request. Contains json of the full trip.
        :type scores: List of dictionnaries / Pandas DataFrame (if csv)
        :param errors: Errors to analyze
        :type errors: List of dictionnaries / Pandas DataFrame (if csv)
        :param errors: Pois' intensities to analyze (if csv)
        :type errors: List of dictionnaries / Pandas DataFrame (if csv)
        """

        self.precision_threshold = 40  # minimal precision to consider processing gps point
        self.breakpoint_threshold = 1  #
        self.data_body = {k: v for k, v in data.items() if k not in 'fixes'}  # metadata contained in raw data
        self.data_events = [fix for fix in data['fixes'] if 'location' not in fix]  # events contained in raw data
        self.data_fixes = self.filter_fixes(data)  # valid fixes to process from raw data
        self.valid_trips = []  # list of trips cleared from null speed issues
        self.nb_trips = 0

    def filter_fixes(self, data):
        return [fix for fix in data['fixes']
                if 'location' in fix
                and fix['location']['precision'] < self.precision_threshold
                and fix['location']['speed'] != -1]

    def preprocess_valid_speed_trips(self):

        null_speed = []
        i_max = len(self.data_fixes) - 1
        delta_timestamp = 600   # minimal duration (10min) of a chunk of null speed to be considered

        #   Group fixes by null speed to get null speed range issue
        for key, group in groupby(enumerate(self.data_fixes), lambda x: x[1]['location']['speed']):
            if key == 0:
                gouped_null_speed_fixes = list(group)
                start_timestamp = gouped_null_speed_fixes[0][1]['timestamp']
                end_timestamp = gouped_null_speed_fixes[-1][1]['timestamp']
                if (end_timestamp - start_timestamp) / 1000 > delta_timestamp:
                    null_speed.append({'start_idx': gouped_null_speed_fixes[0][0],
                                       'end_idx': gouped_null_speed_fixes[-1][0]})

        #   Extract from initial trip cleaned trips without null speed problem
        if len(null_speed) > 1:
            self.valid_trips = [self.data_fixes[item['end_idx']:null_speed[idx + 1]['start_idx']]
                                for idx, item in null_speed if idx < len(null_speed) - 1]
            self.valid_trips.extend([self.data_fixes[0:null_speed[0]['start_idx']],
                                     self.data_fixes[null_speed[-1]['end_idx']:i_max + 1]])
        elif len(null_speed) == 1:
            self.valid_trips = [self.data_fixes[0:null_speed[0]['start_idx']],
                                self.data_fixes[null_speed[-1]['end_idx']:i_max + 1]]
        else:
            self.valid_trips = [self.data_fixes]

        #   Save number of trip to match and return
        self.nb_trips = len(self.valid_trips)

    def call_osrm_match(self, fixes):

        osrm_options = '?steps=true&tidy=true&geometries=geojson&annotations=true&overview=full'
        osrm_api = 'http://localhost:5000/match/v1/driving/{}'
        loc_string = ';'.join('{},{}'.format(str(fix['location']['longitude']), str(fix['location']['latitude']))
                              for fix in fixes)

        #   Call OSRM and return result
        osrm_call = osrm_api.format(loc_string) + osrm_options
        osrm_response = requests.get(osrm_call)
        osrm_trip = osrm_response.json()

        return osrm_trip

    def call_osrm_nearest(self, long, lat):

        osrm_options = '?number=3'
        osrm_api = 'http://localhost:5000/nearest/v1/driving/{},{}'

        #   Call OSRM and return result
        osrm_call = osrm_api.format(long, lat) + osrm_options
        osrm_response = requests.get(osrm_call)
        osrm_near = osrm_response.json()

        return osrm_near

    def nearest_match_idx(self, nearest_match, valid_road_names):

        #   nearest_match are ordered by ascending distance between raw and snapped points
        nearest_idx = -1
        for idx, item in enumerate(nearest_match['waypoints']):
            if item['name'] in valid_road_names:
                nearest_idx = idx
                break
        return nearest_idx

    def call_osrm_route(self, trip_idx, osrm_res, ranges, fix_to_remove_idx):

        osrm_route_intermediary = [fix if fix_idx not in fix_to_remove_idx else None
                                   for fix_idx, fix in enumerate(osrm_res)]
        osrm_options = '?steps=true&geometries=geojson&annotations=true&overview=full&continue_straight=true'
        osrm_api = 'http://localhost:5000/route/v1/driving/{}'

        for item in ranges:
            if item['start'] != item['end']:
                #osrm_route_range = osrm_route_intermediary[item['start']:item['end']+1]
                #osrm_range_intermediary = [fix for fix in osrm_route_intermediary[item['start']:item['end']+1] if fix is not None]
                osrm_range_intermediary = osrm_route_intermediary[item['start']:item['end']+1]
                loc_string = ';'.join('{},{}'.format(str(fix['location'][0]), str(fix['location'][1]))
                                      for fix in osrm_range_intermediary if fix is not None)

                #   Call OSRM routing and return result
                osrm_call = osrm_api.format(loc_string) + osrm_options
                osrm_response = requests.get(osrm_call)
                osrm_trip = osrm_response.json()

                if osrm_trip['code'] == 'Ok':
                    tex_osrm_responses = []
                    valid_road_names = []
                    global_dict = {'global_geometry': osrm_trip['routes'][0]['geometry'],
                                   'global_weight_name': osrm_trip['routes'][0]['weight_name'],
                                   'global_weight': osrm_trip['routes'][0]['weight'],
                                   'global_distance': osrm_trip['routes'][0]['distance'],
                                   'global_duration': osrm_trip['routes'][0]['duration']}

                    for trace_idx, trace in enumerate(osrm_trip['waypoints']):
                        if trace_idx < len(osrm_trip['waypoints'])-1:
                            trace_dict = dict(osrm_trip['routes'][0]['legs'][trace_idx])
                            tex_osrm_dict = dict(trace)
                            tex_osrm_dict['distance2shape'] = tex_osrm_dict.pop('distance')
                            tex_osrm_dict.update(trace_dict)
                            tex_osrm_dict.update(global_dict)
                            tex_osrm_responses.append(tex_osrm_dict)

                            road_name = str(trace['name'])
                            valid_road_names.append(road_name)

                    for fix_idx, fix in enumerate(osrm_range_intermediary):
                        if (fix_idx < len(osrm_range_intermediary)-1 and fix is not None):
                            self.valid_trips[trip_idx][item['start'] + fix_idx]['tex-osrm'] = tex_osrm_responses.pop(0)
                        elif (fix_idx < len(osrm_range_intermediary)-1 and fix is None):
                            long = self.valid_trips[trip_idx][item['start'] + fix_idx]['location']['longitude']
                            lat = self.valid_trips[trip_idx][item['start'] + fix_idx]['location']['latitude']
                            nearest_match = self.call_osrm_nearest(long, lat)
                            nearest_idx = self.nearest_match_idx(nearest_match, valid_road_names)
                            if nearest_idx != -1:
                                nearest_match_dict = dict(nearest_match['waypoints'][nearest_idx])
                                nearest_match_dict['distance2shape'] = nearest_match_dict.pop('distance')
                                nearest_match_dict.update(global_dict)
                                self.valid_trips[trip_idx][item['start'] + fix_idx]['tex-osrm'] = nearest_match_dict
                            else:
                                self.valid_trips[trip_idx][item['start'] + fix_idx]['tex-osrm'] = global_dict
                        else:
                            continue

    def get_osrm_match_ranges(self, osrm_trip):

        seuil = self.breakpoint_threshold
        min_length = 2 + seuil
        osrm_trip_intermediary = [fix if fix is not None else {'matchings_index': -1} for fix in
                                  osrm_trip['tracepoints']]

        legs = defaultdict(list)
        for key, group in groupby(enumerate(osrm_trip_intermediary), lambda x: x[1]['matchings_index']):
            legs[key].extend(list(group))
        legs.pop(-1, None)

        legs_end_idx = [legs[item][-1][0] for item in legs]
        legs_start_idx = [legs[item][0][0] for item in legs if len(legs[item]) > 2]
        fix_to_remove_idx = list(legs_end_idx)
        fix_to_remove_idx.extend(legs_start_idx)

        reduced_leg_ranges = [{'start': legs[item][0][0], 'end': legs[item][-1][0]} if len(legs[item]) < min_length
                              else {'start': legs[item][seuil][0],
                                    'end': legs[item][-(1+seuil)][0]} for item in legs]
        reduced_gap_ranges = [{'start': leg_range['end'], 'end': reduced_leg_ranges[idx+1]['start']}
                              for idx, leg_range in enumerate(reduced_leg_ranges) if idx < len(reduced_leg_ranges) - 1]

        if reduced_leg_ranges[0]['start'] > 0:
            reduced_gap_ranges.append({'start': 0, 'end': reduced_leg_ranges[0]['start']})

        if reduced_leg_ranges[-1]['end'] < len(osrm_trip['tracepoints']) - 1:
            reduced_gap_ranges.append({'start': reduced_leg_ranges[-1]['end'], 'end': len(osrm_trip['tracepoints']) - 1})

        ranges = list(reduced_gap_ranges)
        ranges.extend(reduced_leg_ranges)
        res = {'ranges': ranges, 'fix_to_remove_idx': fix_to_remove_idx}

        return res

    def call_osrm(self, fixes):
        #   TODO: add optionnal parameters to custom osrm_options & api
        osrm_options = '?alternatives=false&steps=true&tidy=true&geometries=geojson&annotations=true&overview=full'
        osrm_options = '?steps=true&geometries=geojson&annotations=true&overview=full&continue_straight=true'
        osrm_api = 'http://localhost:5000/route/v1/driving/{}'
        loc_string = ';'.join('{},{}'.format(str(fix['location']['longitude']), str(fix['location']['latitude']))
                              for fix in fixes)

        #   Call OSRM and return result
        osrm_call = osrm_api.format(loc_string) + osrm_options
        osrm_response = requests.get(osrm_call)
        osrm_trip = osrm_response.json()

        return osrm_trip

    def write_json(self, trip_idx):
        mapmatching_res = self.data_body
        mapmatching_res['fixes'] = self.valid_trips[trip_idx]
        mapmatching_res['nb_valid_fixes'] = len(self.valid_trips[trip_idx])
        print('nb_valid_fixes: {}'.format(len(self.valid_trips[trip_idx])))
        name = 'latest-matched.json'
        with open(name, 'w') as outfile:
            json.dump(mapmatching_res, outfile)
        return mapmatching_res


        #print("Dumped .json results for trip {}".format(mapmatching_res['trip_id']))

    def run(self):
        t4 = dt.datetime.utcnow()
        self.preprocess_valid_speed_trips()
        t5 = dt.datetime.utcnow()
        print("preprocess_valid_point() time: {}".format((t5 - t4).total_seconds()))
        results = []

        for trip_idx in range(self.nb_trips):
            t6 = dt.datetime.utcnow()
            osrm_res = self.call_osrm_match(self.valid_trips[trip_idx])
            t7 = dt.datetime.utcnow()
            print("match() time: {}".format((t7 - t6).total_seconds()))

            if osrm_res['code'] == 'Ok':
                t8 = dt.datetime.utcnow()
                range_dic = self.get_osrm_match_ranges(osrm_res)
                t9 = dt.datetime.utcnow()
                print("get_match_ranges() time: {}".format((t9 - t8).total_seconds()))
                self.call_osrm_route(trip_idx, osrm_res['tracepoints'], range_dic['ranges'],
                                     range_dic['fix_to_remove_idx'])
                t10 = dt.datetime.utcnow()
                print("routing_postprocessing() time: {}".format((t10 - t9).total_seconds()))
                results.append(self.write_json(trip_idx))

        return results





