"""GeoJSON (RFC 7946) geometry models. Coordinates are always [longitude, latitude]."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Longitude = Annotated[float, Field(ge=-180, le=180)]
Latitude = Annotated[float, Field(ge=-90, le=90)]
Position = Annotated[list[float], Field(min_length=2, max_length=3)]


def _check_position(pos: list[float]) -> list[float]:
    lon, lat = pos[0], pos[1]
    if not (-180 <= lon <= 180):
        raise ValueError(f"longitude {lon} out of range [-180, 180] (coordinates must be [lon, lat])")
    if not (-90 <= lat <= 90):
        raise ValueError(f"latitude {lat} out of range [-90, 90] (coordinates must be [lon, lat])")
    return pos


class Point(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["Point"] = "Point"
    coordinates: Position

    @field_validator("coordinates")
    @classmethod
    def _v(cls, v: list[float]) -> list[float]:
        return _check_position(v)

    @property
    def lon(self) -> float:
        return self.coordinates[0]

    @property
    def lat(self) -> float:
        return self.coordinates[1]


class LineString(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["LineString"] = "LineString"
    coordinates: list[Position] = Field(min_length=2)

    @field_validator("coordinates")
    @classmethod
    def _v(cls, v: list[list[float]]) -> list[list[float]]:
        for p in v:
            _check_position(p)
        return v


class Polygon(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["Polygon"] = "Polygon"
    coordinates: list[list[Position]] = Field(min_length=1)

    @field_validator("coordinates")
    @classmethod
    def _v(cls, v: list[list[list[float]]]) -> list[list[list[float]]]:
        for ring in v:
            if len(ring) < 4:
                raise ValueError("polygon ring needs at least 4 positions")
            for p in ring:
                _check_position(p)
            if ring[0][:2] != ring[-1][:2]:
                raise ValueError("polygon ring must be closed")
        return v


class MultiPolygon(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["MultiPolygon"] = "MultiPolygon"
    coordinates: list[list[list[Position]]] = Field(min_length=1)


Geometry = Point | LineString | Polygon | MultiPolygon


class BBox(BaseModel):
    """[min_lon, min_lat, max_lon, max_lat]"""

    model_config = ConfigDict(extra="forbid")
    min_lon: Longitude
    min_lat: Latitude
    max_lon: Longitude
    max_lat: Latitude

    @classmethod
    def parse(cls, value: str) -> BBox:
        parts = [float(p) for p in value.split(",")]
        if len(parts) != 4:
            raise ValueError("bbox must have 4 comma-separated numbers: min_lon,min_lat,max_lon,max_lat")
        b = cls(min_lon=parts[0], min_lat=parts[1], max_lon=parts[2], max_lat=parts[3])
        if b.min_lat > b.max_lat:
            raise ValueError("bbox min_lat > max_lat")
        return b

    def as_list(self) -> list[float]:
        return [self.min_lon, self.min_lat, self.max_lon, self.max_lat]
