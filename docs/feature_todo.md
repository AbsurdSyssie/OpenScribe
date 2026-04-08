# Separate dictation ASR
We should allow for a separate endpoint to be used for when a clinician is dictating, rather than converstational.
The idea here is that we should leverage the medASR from google to capture clinical words that are often messed up with other ASRs (whisper, parakeet).

# Dictation recording option
ALongside the batch and live transcription, we should have an option for dictation which uses the pre-configure dictation endpoint. 
This could be live by default, using the same VAD logic. 
# Post recording dictation prompt
To best capture the clinicians thinking, once they have finished a consult recording, we prompt them to dictate a clinician note at the end to summarise the interaction and record the patients proper clinical entities - such as drugs, conditions and plan. 
This should use the dictation endpoint. 

# Give guidance on quick actions
Need to add a little text box that is optional that steers the quick action response - i.e referral letter quick action, however we should be able to say "to urology" etc

# UI improvement
The clinical notes / follow up area needs a revamp, really ugly and not very useable.
Would like to have a favourites bar that has little icons for sms 

