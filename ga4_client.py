# ga4_client.py
import json
from google.analytics.data_v1beta import BetaAnalyticsDataClient
from google.analytics.data_v1beta.types import DateRange, Dimension, Metric, RunReportRequest
from google.analytics.data_v1beta import GetMetadataRequest
from google.analytics.data_v1beta.types import OrderBy

from google.analytics.data_v1beta.types import FilterExpression, Filter


def load_client_from_service_account(json_keyfile):
    client = BetaAnalyticsDataClient.from_service_account_file(json_keyfile)
    return client


def get_metadata(client: BetaAnalyticsDataClient, property_id: str) -> dict:
    """Fetch GA4 metadata for a property.

    Returns a dict with lists of available dimensions and metrics.
    """
    req = GetMetadataRequest(name=f"properties/{property_id}/metadata")
    md = client.get_metadata(request=req)
    dimensions = [d.api_name for d in md.dimensions]
    metrics = [m.api_name for m in md.metrics]
    return {"dimensions": dimensions, "metrics": metrics}


def validate_fields_against_metadata(
    requested_metrics: list[str],
    requested_dimensions: list[str],
    metadata: dict,
) -> tuple[list[str], list[str], dict]:
    avail_metrics = set(metadata.get("metrics", []))
    avail_dims = set(metadata.get("dimensions", []))

    valid_metrics = [m for m in requested_metrics if m in avail_metrics]
    valid_dims = [d for d in requested_dimensions if d in avail_dims]

    notes = {}
    invalid_m = [m for m in requested_metrics if m not in avail_metrics]
    invalid_d = [d for d in requested_dimensions if d not in avail_dims]
    if invalid_m:
        notes["invalidMetrics"] = invalid_m
    if invalid_d:
        notes["invalidDimensions"] = invalid_d

    return valid_metrics, valid_dims, notes


def _build_dimension_filter(dimension_filters: list[dict] | None):
    """Build a GA4 FilterExpression from a simple list of filters.

    Each filter dict supports:
      - field (dimension api name)
      - op: 'EXACT' | 'CONTAINS'
      - value: string

    Multiple filters are AND-ed.
    """
    if not dimension_filters:
        return None

    expressions = []
    for f in dimension_filters:
        field = f.get("field")
        value = f.get("value")
        op = (f.get("op") or "EXACT").upper()
        if not field or value is None:
            continue

        if op == "CONTAINS":
            string_filter = Filter.StringFilter(
                match_type=Filter.StringFilter.MatchType.CONTAINS,
                value=str(value),
                case_sensitive=False,
            )
        else:
            string_filter = Filter.StringFilter(
                match_type=Filter.StringFilter.MatchType.EXACT,
                value=str(value),
                case_sensitive=False,
            )

        expressions.append(
            FilterExpression(
                filter=Filter(
                    field_name=str(field),
                    string_filter=string_filter,
                )
            )
        )

    if not expressions:
        return None

    if len(expressions) == 1:
        return expressions[0]

    return FilterExpression(and_group=FilterExpression.AndGroup(expressions=expressions))


def run_report(
    client,
    property_id,
    metrics,
    dimensions,
    start_date="14daysAgo",
    end_date="today",
    limit=10000,
    *,
    metadata: dict | None = None,
    validate_with_metadata: bool = False,
    order_by_metric: str | None = None,
    order_desc: bool = True,
    dimension_filters: list[dict] | None = None,
):
    """Run a GA4 Data API report and return a structured dict.

    If validate_with_metadata=True and metadata is provided, invalid metrics/dimensions
    will be removed and returned in `notes`.

    If order_by_metric is provided, results will be ordered by that metric.
    """

    notes: dict = {}

    if validate_with_metadata and metadata is not None:
        metrics, dimensions, vnotes = validate_fields_against_metadata(metrics, dimensions, metadata)
        notes.update(vnotes)

    if not metrics:
        raise ValueError("No valid GA4 metrics selected (after validation).")
    if not dimensions:
        raise ValueError("No valid GA4 dimensions selected (after validation).")

    dm = [Dimension(name=d) for d in dimensions]
    mm = [Metric(name=m) for m in metrics]
    date_range = DateRange(start_date=start_date, end_date=end_date)

    order_bys = None
    if order_by_metric:
        if order_by_metric not in metrics:
            notes.setdefault("orderByIgnored", [])
            notes["orderByIgnored"].append(order_by_metric)
        else:
            order_bys = [
                OrderBy(
                    metric=OrderBy.MetricOrderBy(metric_name=order_by_metric),
                    desc=order_desc,
                )
            ]

    dim_filter_expr = _build_dimension_filter(dimension_filters)

    request = RunReportRequest(
        property=f"properties/{property_id}",
        dimensions=dm,
        metrics=mm,
        date_ranges=[date_range],
        limit=limit,
        order_bys=order_bys,
        dimension_filter=dim_filter_expr,
    )

    try:
        response = client.run_report(request=request)
    except Exception as e:
        raise RuntimeError(
            f"GA4 run_report failed for property {property_id}. "
            f"metrics={metrics}, dimensions={dimensions}, dateRange=({start_date},{end_date}). "
            f"dimensionFilters={dimension_filters}. "
            f"Error: {e}"
        )

    rows = []
    for row in getattr(response, "rows", []) or []:
        rowd = {}
        for i, d in enumerate(dimensions):
            rowd[d] = row.dimension_values[i].value
        for j, m in enumerate(metrics):
            rowd[m] = row.metric_values[j].value
        rows.append(rowd)

    out = {
        "dimensionHeaders": dimensions,
        "metricHeaders": metrics,
        "rows": rows,
    }
    if notes:
        out["notes"] = notes
    if not rows:
        out.setdefault("notes", {})
        out["notes"]["emptyResult"] = True

    return out
