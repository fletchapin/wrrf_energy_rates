import os
import warnings
import numpy as np
import pandas as pd
import datetime as dt
import matplotlib.pyplot as plt
import matplotlib.font_manager as font_manager

# change to repo parent directory and suppress superfluous openpyxl warnings
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
warnings.filterwarnings('ignore', category=UserWarning, module='openpyxl')

def get_charge_array(consumption_data, rate_data, charge_type, utility="electric"):
    """Gets an array with customer, demand, or energy charges (i.e. `charge_type`)
    specific to each day/time

    Parameters
    ----------
    consumption_data : DataFrame
        Baseline electrical usage data. Determines which dates and times
        to gather rate information. Column headers omitted due to size.
        See 'data/baseline_noCHP.csv' for an example.

    rate_data : DataFrame
        Electric and gas billing information

        ==================  ===========================================================
        utility             type of utility {'electric', 'gas'}
        type                type of charge {'customer', 'demand', 'energy'}
        period              period used to calculate demand charge (as `str`)
        basic_charge_limit (imperial)  the limit in imperial units at which the charge comes into effect (as `int`)
        basic_charge_limit (metric)  the limit in metric units at which the charge comes into effect (as `int`)
        month_start         first month for which the charge applies {1-12}
        month_end           last month for which the charge applies {1-12}
        hour_start          hour at which the charge begins {0-24}
        hour_end            hour at which the charge ends {0-24}
        weekday_start       first day on which the charge applies {0-6}
        weekday_end         last day for which the charge applies {0-6}
        charge (imperial)   cost of service per unit gas (therm, therm/hr) or electricity (kW, kWh) (as `int`)
        charge (metric)     cost of service per unit gas (m3, m3/hr) or electricity (kW, kWh)(as `int`)
        units               units of `basic_charge_limit` and `charge` (as `str`)
        ==================  ===========================================================

    charge_type : {'customer', 'demand', 'energy'}
        Type of charge to look up

    utility : {'electric', 'gas'}
        Type of utility to look up

    Raises
    ------
    ValueError
        When invalid `charge_type` is entered

    KeyError
        When `consumption_data` does not have 'DateTime' column

    Returns
    -------
    array
        A structured array of arrays with the name of each array corresponding
        to the basic charge limit which applies to the array of charges
        corresponding to the given utility and charge type and
        for each hour, day, and month
    """
    ndays = int(consumption_data.shape[0] / 96)
    # first search for the correct charge type, then correct utility
    charges = rate_data.loc[(rate_data["type"] == charge_type), :]
    charges = charges.loc[charges["utility"] == utility, :]
    if charge_type == "customer":
        return charges["charge (imperial)"].values
    periods = charges["period"].values
    charge_limits = charges["basic_charge_limit (imperial)"]
    weekdays = consumption_data["DateTime"].dt.weekday.values
    months = consumption_data["DateTime"].dt.month.values
    hours = consumption_data["DateTime"].dt.hour.astype(float).values
    # Make sure hours are being incremented by 15-minute increments
    hours += np.tile(np.arange(4) / 4, 24 * ndays)

    # if no charge was listed for this utility and charge_type, return 0
    if charge_type == "demand":
        if charges.shape[0] == 0:
            data = np.zeros((ndays * 96, 1))
            return np.array(data, dtype=[("0.0", float)])
    elif charge_type == "energy":
        if charges.shape[0] == 0:
            data = np.zeros(ndays * 96)
            return np.array(data, dtype=[("0.0", float)])
    else:
        raise ValueError("Invalid charge_type: " + charge_type)

    charge_array = np.array([])
    for limit in np.unique(charge_limits):
        limit_charges = charges.loc[charges["basic_charge_limit (imperial)"] == limit, :]
        if charge_type == "demand":
            data = np.zeros((ndays * 96, limit_charges.shape[0]))
        else:
            data = np.zeros(ndays * 96)
        periods_seen = {}
        for idx in limit_charges.index:
            charge = limit_charges.loc[idx, :]
            idx_np = idx - min(limit_charges.index.values)
            period = periods[idx_np]
            apply_charge = (
                (months >= charge["month_start"])
                & (months <= charge["month_end"])
                & (weekdays >= charge["weekday_start"])
                & (weekdays <= charge["weekday_end"])
                & (hours >= charge["hour_start"])
                & (hours < charge["hour_end"])
            )
            if charge_type == "demand":
                # Need to make sure to add DR charges for a non-contigious period to the same column!
                if period not in periods_seen:
                    periods_seen[period] = (idx_np, apply_charge)
                else:
                    idx_np = periods_seen[period][0]
                    apply_charge = apply_charge | periods_seen[period][1]
                data[:, idx_np] = apply_charge * charge["charge (imperial)"]
            else:
                data += apply_charge * charge["charge (imperial)"]
        if charge_array.size == 0:
            charge_array = np.array(data, dtype=[(str(limit), float)])
        else:
            new_charge_array = np.empty(
                charge_array.shape, charge_array.dtype.descr + [(str(limit), float)]
            )
            for n in charge_array.dtype.names:
                new_charge_array[n] = charge_array[n]
            new_charge_array[str(limit)] = data

            charge_array = new_charge_array

    return charge_array


def calculate_cost(
    charges,
    consumption_data,
    charge_type="demand",
    utility="electric",
):
    """Calculates the cost of given charges (demand or energy) for the given
    billing rate structure, utility, and consumption information

    Parameters
    ----------
    charges : array
        structured array of arrays with names denoting to charge limit for each array
        of demand or energy charges

    consumption_data : DataFrame
        Baseline electrical or gas usage data. Determines which dates and times
        to gather rate information. Column headers omitted due to size.
        See 'data/baseline_noCHP.csv' for an example.

    charge_type : {'demand', 'energy'}
        Type of charge to calculate costs for

    utility : {'electric', 'gas'}
        Type of utility to calculate costs for

    Raises
    ------
    ValueError
        When invalid `utility` or `charge_type` is entered

    Returns
    -------
    int
        cost in USD for the given `consumption_data`, `charge_type`, and `utility`
    """
    names = charges.dtype.names
    cost = 0

    if charge_type == "demand":
        for j in range(len(names)):
            name = names[j]
            if j == len(names) - 1:
                next_name = None
            else:
                next_name = names[j + 1]
            try:
                for i in range(charges[name].shape[1]):
                    demand = consumption_data[
                        np.argmax(charges[name][:, i] * consumption_data)
                        + consumption_data.index[0]
                    ]
                    if next_name and demand > float(next_name):
                        cost += np.max(
                            (float(next_name) - float(name)) * charges[name][:, i]
                        )
                    else:
                        cost += np.max(
                            [np.max((demand - float(name)) * charges[name][:, i]), 0]
                        )
            except IndexError:
                demand = consumption_data[
                    np.argmax(charges[name][:] * consumption_data)
                    + consumption_data.index[0]
                ]
                if next_name and demand > float(next_name):
                    cost += np.max((float(next_name) - float(name)) * charges[name][:])
                else:
                    cost += np.max(
                        [np.max((demand - float(name)) * charges[name][:]), 0]
                    )
    elif charge_type == "energy":
        if utility == "electric":
            divisor = 4
        elif utility == "gas":
            divisor = 96
        else:
            raise ValueError("Invalid utility: " + utility)

        energy = 0
        start_idx = 0
        for j in range(len(names)):
            name = names[j]
            if j == len(names) - 1:
                cost += np.sum(
                    (consumption_data.iloc[start_idx:] / divisor) * charges[name][start_idx:]
                )
                break
            else:
                next_name = names[j + 1]

            for i in range(start_idx, len(charges[name])):
                energy += consumption_data[i + consumption_data.index[0]]
                if energy / divisor > float(next_name):
                    cost += np.sum(
                        (
                            consumption_data.loc[
                                start_idx
                                + consumption_data.index[0] : i
                                - 1
                                + consumption_data.index[0]
                            ]
                            / divisor
                        )
                        * charges[name][start_idx:i]
                    )
                    cost += (
                        float(next_name)
                        - (
                            (energy - consumption_data[i + consumption_data.index[0]])
                            / divisor
                        )
                    ) * charges[name][i]
                    start_idx = i + 1
                    break
            if i == len(charges[name]) - 1:
                cost += np.sum(
                    (
                        consumption_data.loc[start_idx + consumption_data.index[0] :]
                        / divisor
                    )
                    * charges[name][start_idx:]
                )
                break
    else:
        raise ValueError("Invalid charge_type: " + charge_type)

    return cost


def last_day_of_month(any_day):
    """From: https://stackoverflow.com/questions/42950/how-to-get-the-last-day-of-the-month"""
    # get close to the end of the month for any day, and add 4 days 'over'
    next_month = any_day.replace(day=28) + dt.timedelta(days=4)
    # subtract the number of remaining 'overage' days to get last day of current month, or said programattically said, the previous day of the first of next month
    return next_month - dt.timedelta(days=next_month.day)


elec_col = "grid_to_plant_kW"
ng_col = "natural_gas_therm_per_hr"

# Load energy consumption and rates
energy_df = pd.read_csv("data/synthetic_energy_data.csv")
metadata = pd.read_csv("data/metadata.csv")
results = None

# Use the helper functions above to simulate year of energy cost calculations
for cwns_no in metadata["CWNS_No"]:
    rate_df = pd.read_excel("data/WWTP_Billing.xlsx", sheet_name=str(cwns_no))
    month = 1
    costs = []
    while month < 13:
        # Find start and end index of this month
        energy_df["DateTime"] = pd.to_datetime(energy_df["DateTime"])
        month_start = dt.datetime(2021, month, 1, 0, 0, 0)
        start_idx = (energy_df["DateTime"] == month_start).idxmax()
        month_end = last_day_of_month(dt.datetime(2021, month, 1, 0, 0, 0)) + dt.timedelta(hours=23, minutes=45)
        end_idx = (energy_df["DateTime"] == month_end).idxmax()

        electric_customer_charges = get_charge_array(
            energy_df.loc[start_idx:end_idx],
            rate_df,
            "customer",
            utility="electric"
        )
        electric_demand_charges = get_charge_array(
            energy_df.loc[start_idx:end_idx],
            rate_df,
            "demand",
            utility="electric"
        )
        electric_energy_charges = get_charge_array(
            energy_df.loc[start_idx:end_idx],
            rate_df,
            "energy",
            utility="electric"
        )
        gas_customer_charges = get_charge_array(
            energy_df.loc[start_idx:end_idx],
            rate_df,
            "customer",
            utility="gas"
        )
        gas_energy_charges = get_charge_array(
            energy_df.loc[start_idx:end_idx],
            rate_df,
            "energy",
            utility="gas"
        )
        gas_demand_charges = get_charge_array(
            energy_df.loc[start_idx:end_idx],
            rate_df,
            "demand",
            utility="gas"
        )
        electric_customer_cost = np.sum(electric_customer_charges)
        gas_customer_cost = np.sum(gas_customer_charges)
        electric_demand_cost = calculate_cost(
            electric_demand_charges,
            energy_df.loc[start_idx:end_idx, elec_col],
            charge_type="demand",
            utility="electric",
        )
        electric_energy_cost = calculate_cost(
            electric_energy_charges,
            energy_df.loc[start_idx:end_idx, elec_col],
            charge_type="energy",
            utility="electric",
        )
        gas_demand_cost = calculate_cost(
            gas_demand_charges,
            energy_df.loc[start_idx:end_idx, ng_col],
            charge_type="demand",
            utility="gas",
        )
        gas_energy_cost = calculate_cost(
            gas_energy_charges,
            energy_df.loc[start_idx:end_idx, ng_col],
            charge_type="energy",
            utility="gas",
        )
        cost = {
            "electric_customer": electric_customer_cost,
            "electric_demand": electric_demand_cost,
            "electric_energy": electric_energy_cost,
            "gas_customer": gas_customer_cost,
            "gas_demand": gas_demand_cost,
            "gas_energy": gas_energy_cost,
        }

        costs.append(cost.values())
        month +=1

    if results is not None:
        costs = [cost for charge in costs for cost in charge]
        results = pd.concat([results, pd.Series(costs, index=index)], axis=1)
    else:
        charge_type = [
            "electric_customer", "electric_demand", "electric_energy", "gas_customer", "gas_demand", "gas_energy",
            "electric_customer", "electric_demand", "electric_energy", "gas_customer", "gas_demand", "gas_energy",
            "electric_customer", "electric_demand", "electric_energy", "gas_customer", "gas_demand", "gas_energy",
            "electric_customer", "electric_demand", "electric_energy", "gas_customer", "gas_demand", "gas_energy",
            "electric_customer", "electric_demand", "electric_energy", "gas_customer", "gas_demand", "gas_energy",
            "electric_customer", "electric_demand", "electric_energy", "gas_customer", "gas_demand", "gas_energy",
            "electric_customer", "electric_demand", "electric_energy", "gas_customer", "gas_demand", "gas_energy",
            "electric_customer", "electric_demand", "electric_energy", "gas_customer", "gas_demand", "gas_energy",
            "electric_customer", "electric_demand", "electric_energy", "gas_customer", "gas_demand", "gas_energy",
            "electric_customer", "electric_demand", "electric_energy", "gas_customer", "gas_demand", "gas_energy",
            "electric_customer", "electric_demand", "electric_energy", "gas_customer", "gas_demand", "gas_energy",
            "electric_customer", "electric_demand", "electric_energy", "gas_customer", "gas_demand", "gas_energy",
        ]
        month = [
            "Jan", "Jan", "Jan", "Jan", "Jan", "Jan",
            "Feb", "Feb", "Feb", "Feb", "Feb", "Feb",
            "Mar", "Mar", "Mar", "Mar", "Mar", "Mar",
            "Apr", "Apr", "Apr", "Apr", "Apr", "Apr",
            "May", "May", "May", "May", "May", "May",
            "June", "June", "June", "June", "June", "June",
            "July", "July", "July", "July", "July", "July",
            "Aug", "Aug", "Aug", "Aug", "Aug", "Aug",
            "Sept", "Sept", "Sept", "Sept", "Sept", "Sept",
            "Oct", "Oct", "Oct", "Oct", "Oct", "Oct",
            "Nov", "Nov", "Nov", "Nov", "Nov", "Nov",
            "Dec", "Dec", "Dec", "Dec", "Dec", "Dec",
        ]
        tuples = list(zip(*[charge_type, month]))
        index = pd.MultiIndex.from_tuples(tuples, names=["charge_type", "month"])

        # flatten lists of costs
        costs = [cost for charge in costs for cost in charge]
        results = pd.Series(costs, index=index)

    # plot all months of sample Facility No. 12000017028
    if cwns_no == 12000017027:
        facility_costs = pd.Series(costs, index=index)
        ind = np.arange(12)  # the x locations for the groups
        width = 0.4          # the width of the bars

        plt.figure(figsize=(8, 8))
        ax0 = plt.gca()
        ax0.bar(ind, facility_costs["electric_energy"], width)
        ax0.bar(ind + width, facility_costs["electric_demand"], width)
        ax0.set_xticks(
            np.arange(12),
            ["Jan", "Feb", "Mar", "Apr", "May", "June", "July", "Aug", "Sept", "Oct", "Nov", "Dec"],
            fontname="Arial",
            fontsize=16
        )
        ax0.set_xlabel("Month", fontname="Arial", fontsize=24)
        ax0.set_ylabel("Electricity cost ($)", fontname="Arial", fontsize=24)
        arial_font = font_manager.FontProperties(family='Arial', style='normal', size=18)
        ax0.legend(["Energy", "Demand"], loc="upper center", frameon=False, prop=arial_font, ncol=2)
        plt.yticks(range(0, 11000, 1000), fontsize=18)
        plt.savefig("ElectricityCosts.png", bbox_inches="tight")

        plt.figure(figsize=(8, 8))
        ax1 = plt.gca()
        ax1.bar(ind, facility_costs["gas_demand"], width)
        ax1.bar(ind + width, facility_costs["gas_energy"], width)
        ax1.set_xticks(
            np.arange(12),
            ["Jan", "Feb", "Mar", "Apr", "May", "June", "July", "Aug", "Sept", "Oct", "Nov", "Dec"],
            fontname="Arial",
            fontsize=16
        )
        ax1.set_xlabel("Month", fontname="Arial", fontsize=24)
        ax1.set_ylabel("Natural gas cost ($)", fontname="Arial", fontsize=24)
        arial_font = font_manager.FontProperties(family='Arial', style='normal', size=18)
        ax1.legend(["Energy", "Demand"], loc="upper center", frameon=False, prop=arial_font, ncol=2)
        plt.yticks(range(0, 400, 50), fontsize=18)
        plt.savefig("NaturalGasCosts.png", bbox_inches="tight")

# violin plot of monthly averages for all rate types and facilities
elec_customer_avg = results.loc[(results.index.get_level_values('charge_type') == 'electric_customer')].mean(axis=0).reset_index(drop=True)
elec_energy_avg = results.loc[(results.index.get_level_values('charge_type') == 'electric_energy')].mean(axis=0).reset_index(drop=True)
elec_demand_avg = results.loc[(results.index.get_level_values('charge_type') == 'electric_demand')].mean(axis=0).reset_index(drop=True)
gas_customer_avg = results.loc[(results.index.get_level_values('charge_type') == 'gas_customer')].mean(axis=0).reset_index(drop=True)
gas_energy_avg = results.loc[(results.index.get_level_values('charge_type') == 'gas_energy')].mean(axis=0).reset_index(drop=True)
gas_demand_avg = results.loc[(results.index.get_level_values('charge_type') == 'gas_demand')].mean(axis=0).reset_index(drop=True)

elec_results = pd.concat([elec_energy_avg, elec_demand_avg, elec_customer_avg], axis=1)
gas_results = pd.concat([gas_energy_avg, gas_demand_avg,gas_customer_avg], axis=1)

plt.figure(figsize=(8, 8))
plt.violinplot(elec_results, quantiles=[[0.25, 0.5, 0.75], [0.25, 0.5, 0.75], [0.25, 0.5, 0.75]])
ax0 = plt.gca()
ax0.set_ylabel("Cost ($/month)", fontname="Arial", fontsize=24)
ax0.set_xticks([1, 2, 3])
ax0.set_xticklabels(
    ["Electric energy\ncharges", "Electric demand \n charges", "Electric customer\ncharges"],
    fontname="Arial",
    fontsize=20
)
plt.yticks(np.arange(0, 51000, step=5000), fontsize=16)
plt.savefig("ElectricViolinPlot.png", bbox_inches="tight")

plt.figure(figsize=(8, 8))
plt.violinplot(gas_results, quantiles=[[0.25, 0.5, 0.75], [0.25, 0.5, 0.75], [0.25, 0.5, 0.75]])
ax0 = plt.gca()
ax0.set_ylabel("Cost ($/month)", fontname="Arial", fontsize=24)
ax0.set_xticks([1, 2, 3])
ax0.set_xticklabels(
    ["Gas energy\ncharges", "Gas demand\ncharges", "Gas customer\ncharges"],
    fontname="Arial",
    fontsize=20
)
plt.yticks(np.arange(0, 3000, step=250), fontsize=16)
plt.savefig("GasViolinPlot.png", bbox_inches="tight")
