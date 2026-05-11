Fix to give the agent

Update the STT model field in both admin.html and admin2.html.

Wherever the STT form currently has a text input like:

<input name="provider_model" ...>

or:

<input name="model_name" ...>

replace it with a conditional dropdown:

{% if stt_form.available_model_options %}
  <label>
    Default model
    <select name="model_name">
      {% for option in stt_form.available_model_options %}
        <option
          value="{{ option.id }}"
          {% if option.id == stt_form.model_name %}selected{% endif %}
        >
          {{ option.label or option.id }}
        </option>
      {% endfor %}
    </select>
  </label>
{% else %}
  <label>
    Model
    <input
      type="text"
      name="model_name"
      value="{{ stt_form.model_name }}"
      placeholder="Enter model name"
    >
  </label>
{% endif %}

If the form is the old upsert form and still expects provider_model, use that name instead:

<select name="provider_model">

For the draft finalization route, use whatever the route expects. From the planned schema, it should be model_name.

Also check this

If the flow is:

Check API key and find transcription options
→ create draft config
→ redirect back to selected draft config

then the template will use the saved-config branch of stt_form_defaults(), not the inspection branch. That is fine because saved configs rebuild available_model_options from config.available_models_json.

So if the dropdown still does not appear after the template change, check whether create_stt_config_draft() is saving:

available_models_json = list(inspection.available_models)

for Deepgram. Based on the implementation direction, it should be.

Recommended test

Add an admin UI test, not just an API test:

Create Deepgram draft with mocked /v1/models returning nova-3 and nova-2
Open admin STT setup page for the draft
Assert model field is a <select>
Assert options include nova-3 and nova-2
Assert API key field is not shown

Current backend tests can prove discovery works, but they will not catch the missing dropdown because the latest commit did not touch admin templates.