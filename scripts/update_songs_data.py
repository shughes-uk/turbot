#!/usr/bin/env python3

from pathlib import Path

import requests
from bs4 import BeautifulSoup
from unidecode import unidecode

page = requests.get(
    "https://animalcrossing.fandom.com/wiki/K.K._Slider_song_list_(New_Horizons)"
)
tree = BeautifulSoup(page.content, "lxml")


with open(Path("src") / "turbot" / "data" / "songs.txt", "w", newline="") as out:

    def data_from(item):
        title = item.select("a")[1]
        return unidecode(title.text).lower()

    table_tag = tree.select("table")[1]
    data = [
        [data_from(item) for item in row_data.select("td")]
        for row_data in table_tag.select("tr")
    ]

    for row in data:
        for title in row:
            out.write(f"{title}\n")
