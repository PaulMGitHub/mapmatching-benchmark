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


import json
import os
import folium
import numpy as np

from flask import Flask
from flask import jsonify
from flask import render_template
from flask import request
from flask import url_for

app = Flask(__name__)

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
                                    'precision': gps_lng_noise + gps_lat_noise}
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
        #mapmatch(trip_to_match)
        return render_template('form.html')
    else:
        overview_path = response['routes'][0]['overview_path']
        synthetic_gps_trace, trip_to_match = make_synthetic_gps_trace(overview_path, noise, sampling)
        write_folium_google_input_trip(overview_path, synthetic_gps_trace)
        #mapmatch(trip_to_match)
        return render_template('google_input_latest.html')


app.run()

