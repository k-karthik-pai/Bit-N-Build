"""Deterministic material-to-buyer matching for the CIRCUIT pipeline."""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MATERIALS_PATH = REPOSITORY_ROOT / "data" / "materials.json"
DEFAULT_BUYERS_PATH = REPOSITORY_ROOT / "data" / "buyers.json"
QUALITY_RULE_PATTERN = re.compile(
    r"^(?P<component>[A-Za-z][A-Za-z0-9]*)_(?P<operator>min|max)_pct$"
)


class CircularityError(Exception):
    """Base class for errors that can be presented safely to a caller."""


class RequestValidationError(CircularityError):
    """Raised when the matching request does not satisfy the frozen contract."""


class DataValidationError(CircularityError):
    """Raised when a dataset cannot be loaded or violates the expected schema."""


def _is_number(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _required_string(record: Mapping[str, Any], field: str, context: str) -> str:
    value = record.get(field)
    if not isinstance(value, str) or not value.strip():
        raise DataValidationError(f"{context}.{field} must be a non-empty string")
    return value


def _validate_requirements(requirements: object, context: str) -> dict[str, float]:
    if not isinstance(requirements, Mapping):
        raise DataValidationError(f"{context} must be an object")

    validated: dict[str, float] = {}
    for rule, threshold in requirements.items():
        if not isinstance(rule, str) or QUALITY_RULE_PATTERN.fullmatch(rule) is None:
            raise DataValidationError(
                f"{context} contains unsupported quality rule {rule!r}; "
                "expected <component>_min_pct or <component>_max_pct"
            )
        if not _is_number(threshold):
            raise DataValidationError(f"{context}.{rule} must be a finite number")
        numeric_threshold = float(threshold)
        if not 0 <= numeric_threshold <= 100:
            raise DataValidationError(
                f"{context}.{rule} must be between 0 and 100 percent"
            )
        validated[rule] = numeric_threshold
    return validated


def _validate_materials(materials: object) -> list[dict[str, Any]]:
    if not isinstance(materials, list):
        raise DataValidationError("materials dataset must be a list")

    validated: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for index, material in enumerate(materials):
        context = f"materials[{index}]"
        if not isinstance(material, Mapping):
            raise DataValidationError(f"{context} must be an object")

        material_id = _required_string(material, "material_id", context)
        if material_id in seen_ids:
            raise DataValidationError(f"duplicate material_id {material_id!r}")
        seen_ids.add(material_id)

        composition = material.get("composition_pct")
        if not isinstance(composition, Mapping):
            raise DataValidationError(f"{context}.composition_pct must be an object")
        validated_composition: dict[str, float] = {}
        for component, percentage in composition.items():
            if not isinstance(component, str) or not component:
                raise DataValidationError(
                    f"{context}.composition_pct keys must be non-empty strings"
                )
            if not _is_number(percentage):
                raise DataValidationError(
                    f"{context}.composition_pct.{component} must be a finite number"
                )
            numeric_percentage = float(percentage)
            if not 0 <= numeric_percentage <= 100:
                raise DataValidationError(
                    f"{context}.composition_pct.{component} must be between "
                    "0 and 100 percent"
                )
            validated_composition[component] = numeric_percentage

        applications = material.get("applications")
        if not isinstance(applications, list):
            raise DataValidationError(f"{context}.applications must be a list")
        validated_applications: list[dict[str, Any]] = []
        for application_index, application in enumerate(applications):
            application_context = f"{context}.applications[{application_index}]"
            if not isinstance(application, Mapping):
                raise DataValidationError(f"{application_context} must be an object")
            application_name = _required_string(
                application, "application", application_context
            )
            requirements = _validate_requirements(
                application.get("min_quality_requirements"),
                f"{application_context}.min_quality_requirements",
            )
            validated_applications.append(
                {
                    "application": application_name,
                    "requirements": requirements,
                }
            )

        validated.append(
            {
                "material_id": material_id,
                "composition": validated_composition,
                "applications": validated_applications,
            }
        )
    return validated


def _validate_buyers(buyers: object) -> list[dict[str, Any]]:
    if not isinstance(buyers, list):
        raise DataValidationError("buyers dataset must be a list")

    validated: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for index, buyer in enumerate(buyers):
        context = f"buyers[{index}]"
        if not isinstance(buyer, Mapping):
            raise DataValidationError(f"{context} must be an object")

        buyer_id = _required_string(buyer, "buyer_id", context)
        if buyer_id in seen_ids:
            raise DataValidationError(f"duplicate buyer_id {buyer_id!r}")
        seen_ids.add(buyer_id)

        material_required_id = _required_string(
            buyer, "material_required_id", context
        )
        annual_demand = buyer.get("annual_demand_tonnes")
        if not _is_number(annual_demand) or float(annual_demand) < 0:
            raise DataValidationError(
                f"{context}.annual_demand_tonnes must be a non-negative finite number"
            )
        requirements = _validate_requirements(
            buyer.get("min_quality_requirements"),
            f"{context}.min_quality_requirements",
        )

        validated.append(
            {
                "buyer_id": buyer_id,
                "material_required_id": material_required_id,
                "annual_demand_tonnes": float(annual_demand),
                "requirements": requirements,
            }
        )
    return validated


def _validate_request(request: object) -> tuple[str, float, str]:
    if not isinstance(request, Mapping):
        raise RequestValidationError("request must be an object")

    material_id = request.get("material_id")
    if not isinstance(material_id, str) or not material_id.strip():
        raise RequestValidationError("material_id must be a non-empty string")

    seller_id = request.get("seller_id")
    if not isinstance(seller_id, str) or not seller_id.strip():
        raise RequestValidationError("seller_id must be a non-empty string")

    quantity = request.get("quantity_tonnes")
    if not _is_number(quantity) or float(quantity) <= 0:
        raise RequestValidationError("quantity_tonnes must be a positive finite number")

    return material_id, float(quantity), seller_id


def _evaluate_requirements(
    composition: Mapping[str, float], requirements: Mapping[str, float]
) -> tuple[bool, list[str]]:
    checks: list[str] = []
    for rule, threshold in requirements.items():
        match = QUALITY_RULE_PATTERN.fullmatch(rule)
        if match is None:  # Requirements are validated before evaluation.
            raise DataValidationError(f"unsupported quality rule {rule!r}")

        component = match.group("component")
        actual = composition.get(component)
        if actual is None:
            return False, checks

        operator = match.group("operator")
        passed = actual >= threshold if operator == "min" else actual <= threshold
        symbol = ">=" if operator == "min" else "<="
        checks.append(f"{component} {actual:g}% {symbol} {threshold:g}%")
        if not passed:
            return False, checks
    return True, checks


def _select_application(
    material: Mapping[str, Any], buyer_requirements: Mapping[str, float]
) -> tuple[str, list[str]] | None:
    composition = material["composition"]
    buyer_passed, buyer_checks = _evaluate_requirements(
        composition, buyer_requirements
    )
    if not buyer_passed:
        return None

    best_match: tuple[str, list[str]] | None = None
    best_specificity = -1
    for application in material["applications"]:
        application_passed, application_checks = _evaluate_requirements(
            composition, application["requirements"]
        )
        if not application_passed:
            continue

        specificity = len(
            set(buyer_requirements) | set(application["requirements"])
        )
        if specificity > best_specificity:
            best_specificity = specificity
            best_match = (
                application["application"],
                list(dict.fromkeys(application_checks + buyer_checks)),
            )
    return best_match


def _format_tonnes(value: float) -> str:
    return f"{value:,.0f}" if value.is_integer() else f"{value:,.2f}"


def find_candidates(
    request: Mapping[str, Any],
    materials: Sequence[Mapping[str, Any]],
    buyers: Sequence[Mapping[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """Return compatible buyers ranked by the share of requested demand they absorb."""

    material_id, quantity, _seller_id = _validate_request(request)
    validated_materials = _validate_materials(materials)
    validated_buyers = _validate_buyers(buyers)

    material = next(
        (
            candidate
            for candidate in validated_materials
            if candidate["material_id"] == material_id
        ),
        None,
    )
    if material is None:
        raise RequestValidationError(f"material_id {material_id!r} was not found")

    candidates: list[dict[str, Any]] = []
    for buyer in validated_buyers:
        if buyer["material_required_id"] != material_id:
            continue
        if buyer["annual_demand_tonnes"] <= 0:
            continue

        application_match = _select_application(material, buyer["requirements"])
        if application_match is None:
            continue
        application, quality_checks = application_match

        covered_tonnes = min(buyer["annual_demand_tonnes"], quantity)
        compatibility_score = round(covered_tonnes / quantity, 4)
        quality_note = (
            "; quality verified: " + ", ".join(quality_checks)
            if quality_checks
            else "; no numeric quality constraints supplied"
        )
        notes = (
            f"Application {application}{quality_note}; demand coverage "
            f"{compatibility_score * 100:.2f}% "
            f"({_format_tonnes(covered_tonnes)} of {_format_tonnes(quantity)} tonnes)."
        )
        candidates.append(
            {
                "buyer_id": buyer["buyer_id"],
                "application": application,
                "compatibility_score": compatibility_score,
                "notes": notes,
            }
        )

    candidates.sort(
        key=lambda candidate: (
            -candidate["compatibility_score"],
            candidate["buyer_id"],
        )
    )
    return {"candidates": candidates}


def _load_json(path: str | Path, dataset_name: str) -> Any:
    dataset_path = Path(path)
    try:
        with dataset_path.open("r", encoding="utf-8") as file:
            return json.load(file)
    except FileNotFoundError as error:
        raise DataValidationError(
            f"{dataset_name} dataset not found: {dataset_path}"
        ) from error
    except OSError as error:
        raise DataValidationError(
            f"could not read {dataset_name} dataset {dataset_path}: {error}"
        ) from error
    except json.JSONDecodeError as error:
        raise DataValidationError(
            f"invalid JSON in {dataset_name} dataset {dataset_path}: "
            f"line {error.lineno}, column {error.colno}"
        ) from error


def run_from_files(
    request: Mapping[str, Any],
    materials_path: str | Path = DEFAULT_MATERIALS_PATH,
    buyers_path: str | Path = DEFAULT_BUYERS_PATH,
) -> dict[str, list[dict[str, Any]]]:
    """Load datasets and execute matching using the frozen request contract."""

    materials = _load_json(materials_path, "materials")
    buyers = _load_json(buyers_path, "buyers")
    return find_candidates(request, materials, buyers)


def run_circularity(
    request: Mapping[str, Any],
    materials_path: str | Path = DEFAULT_MATERIALS_PATH,
    buyers_path: str | Path = DEFAULT_BUYERS_PATH,
) -> dict[str, list[dict[str, Any]]]:
    """Contract-compatible alias for orchestrator wiring."""

    return run_from_files(request, materials_path, buyers_path)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Rank compatible buyers for a material and quantity."
    )
    parser.add_argument("--material-id", required=True)
    parser.add_argument("--quantity-tonnes", required=True, type=float)
    parser.add_argument("--seller-id", required=True)
    parser.add_argument("--materials", default=str(DEFAULT_MATERIALS_PATH))
    parser.add_argument("--buyers", default=str(DEFAULT_BUYERS_PATH))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    arguments = parser.parse_args(argv)
    request = {
        "material_id": arguments.material_id,
        "quantity_tonnes": arguments.quantity_tonnes,
        "seller_id": arguments.seller_id,
    }
    try:
        result = run_from_files(request, arguments.materials, arguments.buyers)
    except CircularityError as error:
        print(f"circularity agent error: {error}", file=sys.stderr)
        return 1

    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
