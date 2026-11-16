{# Lower-case names of the columns the month's export has (older exports lack newer columns). #}
{% macro source_columns() %}
    {% if execute %}
        {% set res = run_query("describe select * from " ~ source('tlc', 'yellow_trips')) %}
        {{ return(res.columns[0].values() | map('lower') | list) }}
    {% endif %}
    {{ return([]) }}
{% endmacro %}
