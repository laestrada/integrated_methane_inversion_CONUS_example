import xarray as xr
import os
import sys
import numpy as np
import yaml

sys.path.append("../inversion_scripts/")
from utils import sum_total_emissions


def prepare_sf(config_path, period_number, base_directory, nudge_factor):
    """
    Function to prepare scale factors for HEMCO emissions. 
    
    This is done at the beginning of an inversion, to generate the appropriate scale factors for the 
    prior simulation. In the first period, the scale factors are just unit scale factors (i.e., use 
    the emissions from HEMCO directly). In following periods, the scale factors are derived from 
    previous inversion results, including nudging to the original prior emission estimates.

    Arguments
        period_number   [int]   : What period are we on? For the first period, period_number = 1
        base_directory  [str]   : The base directory for the inversion, where e.g., "preview_sim/" resides
        nudge_factor    [float] : Weight applied to original prior when nudging (default = 0.1)
    """

    # Read config file
    config = yaml.load(open(config_path), Loader=yaml.FullLoader)

    # Fix nudge_factor type
    nudge_factor = float(nudge_factor)

    # Define some useful paths
    unit_sf_path = os.path.join(base_directory, "unit_sf.nc")
    statevector_path = os.path.join(base_directory, "StateVector.nc")
    preview_cache = os.path.join(base_directory, "preview_run/OutputDir")
    jacobian_dir = os.path.join(base_directory, "jacobian_runs")
    prior_sim = [r for r in os.listdir(jacobian_dir) if "0000" in r][0]
    prior_cache = os.path.join(base_directory, f"jacobian_runs/{prior_sim}/OutputDir")
    diags_file = [f for f in os.listdir(preview_cache) if "HEMCO_diagnostics" in f][0]
    diags_path = os.path.join(preview_cache, diags_file)

    # Get state vector, grid-cell areas, mask
    statevector = xr.load_dataset(statevector_path)
    areas = xr.load_dataset(diags_path)["AREA"]
    state_vector_labels = statevector["StateVector"]
    last_ROI_element = int(
        np.nanmax(state_vector_labels.values) - config["nBufferClusters"]
    )
    mask = state_vector_labels <= last_ROI_element

    # Get original emissions from preview, for first inversion period
    original_emis = xr.load_dataset(diags_path)
    original_emis = original_emis["EmisCH4_Total"].isel(time=0, drop=True)

    # Initialize unit scale factors
    sf = xr.load_dataset(unit_sf_path)

    # If we are past the first inversion period, need to use previous inversion results to construct
    # the initial scale factors for the current period.
    period_number = int(period_number)
    if period_number > 1:

        # List all available HEMCO diagnostic files from the prior simulation
        hemco_list = [f for f in os.listdir(prior_cache) if "HEMCO" in f]
        hemco_list.sort()

        # For each period up to (but not including) the current one
        for p in range(period_number - 1):

            # Add one since we're counting from period 1, not 0
            p = p + 1

            # Get the original HEMCO emissions for period p
            hemco_emis_path = os.path.join(prior_cache, hemco_list[p - 1])  # p-1 index
            original_emis = xr.load_dataset(hemco_emis_path)
            original_emis = original_emis["EmisCH4_Total"].isel(time=0, drop=True)

            # Get the gridded posterior for period p
            gridded_posterior_path = os.path.join(
                base_directory, f"kf_inversions/period{p}/gridded_posterior.nc"
            )
            posterior_p = xr.load_dataset(gridded_posterior_path)

            # Get posterior emissions multiplied up to current period p, and apply nudging
            current_posterior_emis = (
                posterior_p["ScaleFactor"] * sf["ScaleFactor"] * original_emis
            )
            nudged_posterior_emis = (
                nudge_factor * original_emis
                + (1 - nudge_factor) * current_posterior_emis
            )  # TODO nudge_factor is currently inverse of what's in the paper, i.e. 0.1 instead of 0.9

            # Sum emissions
            current_total = sum_total_emissions(current_posterior_emis, areas, mask)
            nudged_total = sum_total_emissions(nudged_posterior_emis, areas, mask)

            # Get the final posterior emissions
            lambda_scaler = current_total / nudged_total
            scaled_nudged_posterior_emis = nudged_posterior_emis * lambda_scaler

            # Get the final posterior scale factors
            sf["ScaleFactor"] = scaled_nudged_posterior_emis / original_emis

            # Reset buffer area to 1 # TODO Do we want this feature?
            # sf["SF_Nonwetland"] = sf["SF_Nonwetland"].where(sf["Clusters"] <= 235)  # Replace buffers with nan
            # sf["SF_Nonwetland"] = sf["SF_Nonwetland"].fillna(1)  # Fill nan with 1

        print(
            f"Used HEMCO emissions up to week {p} to prepare prior scaling factors for this week."
        )

    # Print the current total emissions in the region of interest
    emis = sf["ScaleFactor"] * original_emis
    total_emis = sum_total_emissions(emis, areas, mask)
    print(f"Total prior emission = {total_emis} Tg a-1")

    # Ensure good netcdf attributes for HEMCO
    sf.lat.attrs["units"] = "degrees_north"
    sf.lat.attrs["long_name"] = "Latitude"
    sf.lon.attrs["units"] = "degrees_east"
    sf.lon.attrs["long_name"] = "Longitude"
    sf.ScaleFactor.attrs["units"] = "1"

    # Save final scale factors
    save_path = os.path.join(base_directory, "ScaleFactors.nc")
    sf.to_netcdf(save_path)

    # Archive scale factors
    archive_path = os.path.join(base_directory, "archive_sf")
    sf.to_netcdf(os.path.join(archive_path, f"prior_sf_period{period_number}.nc"))


if __name__ == "__main__":
    import sys

    config_path = sys.argv[1]
    period_number = sys.argv[2]
    base_directory = sys.argv[3]
    nudge_factor = sys.argv[4]

    prepare_sf(config_path, period_number, base_directory, nudge_factor)
