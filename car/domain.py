import math
from pydantic import BaseModel, Field, ConfigDict, field_validator
from typing import Literal

class Input(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid', allow_inf_nan=False)

    @field_validator('phone', check_fields=False)
    @classmethod
    def valid_phone(cls, value):
        if value is not None and not 7 <= sum(c.isdigit() for c in value) <= 15:
            raise ValueError('Phone must contain 7 to 15 digits')
        return value


class Vehicle(Input):
    distance_unit: Literal['mi', 'km'] = 'mi'
    make: str = Field(min_length=1, max_length=60)
    model: str = Field(min_length=1, max_length=60)
    year: int = Field(ge=1980, le=2030)
    odometer: float = Field(ge=0, le=2000000)
    mpg: float = Field(gt=0, le=200)
    fuel_price: float = Field(ge=0, le=30)
    oil_due: float = Field(ge=0, le=2000000)

class TripStart(Input):
    source: Literal['gps', 'simulated']

class Point(Input):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    accuracy: float = Field(ge=0, le=5000)
    timestamp: float = Field(gt=0)

class Finish(Input):
    tolls: float = Field(ge=0, le=1000)

class Expense(Input):
    category: Literal['gasolina', 'peajes', 'mantenimiento', 'otros']
    amount: float = Field(gt=0, le=100000)
    note: str = Field(max_length=200)

class Question(Input):
    language: Literal['en', 'es'] = 'es'
    question: str = Field(min_length=1, max_length=1000)

class Plan(Input):
    plan: Literal['basic', 'pro']

class AddressFields(Input):
    preferred_name: str | None = Field(default=None, min_length=1, max_length=40)
    city: str | None = Field(default=None, max_length=100)
    state: str | None = Field(default=None, max_length=100)
    country: str | None = Field(default=None, max_length=100)

class Profile(AddressFields):
    name: str = Field(min_length=1, max_length=80)
    address: str | None = Field(default=None, min_length=5, max_length=300)
    phone: str | None = Field(default=None, min_length=7, max_length=30, pattern=r'^\+?[0-9 () .-]+$')

class Onboarding(AddressFields):
    name: str = Field(min_length=1, max_length=80)
    address: str = Field(min_length=5, max_length=300)
    phone: str = Field(min_length=7, max_length=30, pattern=r'^\+?[0-9 () .-]+$')
    make: str = Field(min_length=1, max_length=60)
    model: str = Field(min_length=1, max_length=60)
    year: int = Field(ge=1980, le=2030)
    initial_odometer: float = Field(ge=0, le=2000000)
    distance_unit: Literal['mi', 'km']


class Maintenance(Input):
    interval: float = Field(gt=0, le=100000)

def miles(a, b):
    la, lb = math.radians(a['lat']), math.radians(b['lat'])
    dl = math.radians(b['lon']-a['lon']); dp = lb-la
    h = math.sin(dp/2)**2 + math.cos(la)*math.cos(lb)*math.sin(dl/2)**2
    return 3958.7613 * 2 * math.asin(min(1, math.sqrt(h)))
