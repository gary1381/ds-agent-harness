{# The month being built is the var `month` (YYYY-MM); a trip belongs to the month its pickup falls in. #}
{% macro month_start() %}timestamp '{{ var("month") }}-01'{% endmacro %}
{% macro month_end() %}(timestamp '{{ var("month") }}-01' + interval 1 month){% endmacro %}
