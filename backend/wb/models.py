from dataclasses import dataclass


@dataclass
class WBProduct:
    id: int
    name: str
    brand: str
    seller: str
    rating: float
    reviews: int
    photos: int