# MRI Decay Viewer

Local viewer for the BME MRI lab DICOM data. It scans the GRE and SE magnitude
series, lets you move through slices/echoes, and estimates T2 or T2* from the
selected pixel or circular ROI. The echo control has a current-echo handle and
two fit-range handles; only echoes inside the selected fit range are used for
the decay fit. Viewer images use one fixed display window across echoes by
default; enable Auto window to stretch each displayed echo separately. The
default ROI radius is 3 pixels. The map below the image uses the current slice,
fit range, and ROI radius to display per-pixel T2 or T2* values. GRE series
also show an approximate Water/Fat Map from the available magnitude multi-echo
data on a fixed 0-100% fat scale.

SE T2 fitting uses a nonlinear offset model, `S = S0 * exp(-TE/T2) + C`, to
avoid overestimating T2 when late echoes flatten near a magnitude/noise floor.
GRE T2* fitting uses the six available echoes with a zero-offset log-linear fit;
very long T2* values are flagged as weakly constrained because the GRE TE range
is short. Point SE fits use nonlinear optimization; the SE map uses the same
offset model on a dense vectorized T2 grid to keep map generation responsive.
The water-fat map uses a room-temperature multi-peak triglyceride spectrum and
a grid fit for fat fraction and a shared GRE T2* term. It is intended for
within-experiment comparison rather than a fully corrected PDFF measurement.

## Run

```bash
python -m pip install -r requirements.txt
python app.py
```

Open http://127.0.0.1:8000.

## Data Included

The app ignores localizers and GRE phase images. The usable magnitude series are:

- GRE FA 9, TR 1928 ms, TE 1.33-10.75 ms
- GRE FA 9, TR 2299.2 ms, TE 2.41-13.06 ms
- GRE FA 20, TR 2299.2 ms, TE 2.41-13.06 ms
- SE TR 1500 ms, TE 15-480 ms
- SE TR 5000 ms, TE 15-480 ms
