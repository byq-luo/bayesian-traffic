########################################################################################################################
# Module: inference/resampling.py
# Description: Resampling schemes for converting weighted particles (series of positions/edges/distances) to
#              unweighted. Notably multinomial resampling and fixed-lag resampling (with stitching).
#
# Web: https://github.com/SamDuffield/bayesian-traffic
########################################################################################################################

import numpy as np

from inference.model import distance_prior
from inference.particles import MMParticles
from tools.edges import get_geometry


def multinomial(particles, weights):
    """
    Full multinomial resampling scheme. Lengths of particles and weights must conform.
    :param particles: list-like or MMParticles object (from inference.smc)
        collection of objects to to resample
    :param weights: list-like
        resampling probabilities
    :return: list-like or MMParticles object
        unweighted collection of objects in the same form as input
    """

    # Number of samples
    n = len(weights)

    # Check weights are normalised
    weights_sum = sum(weights)
    if weights_sum != 1:
        weights /= weights_sum

    # Sample indices according to weights (with replacement)
    sampled_indices = np.random.choice(n, n, replace=True, p=weights)

    # Update and output particles
    if isinstance(particles, MMParticles):
        if particles.n != n:
            raise ValueError("Length of MMParticles to be resampled and weights do not conform")
        out_particles = particles.copy()
        out_particles.particles = [out_particles.particles[i] for i in sampled_indices]
    elif isinstance(particles, np.ndarray):
        if len(particles) != n:
            raise ValueError("Length of particles (numpy.ndarray) to be resampled and weights do not conform")
        out_particles = particles[sampled_indices]
    else:
        if len(particles) != n:
            raise ValueError("Length of particles to be resampled and weights do not conform")
        out_particles = [particles[i] for i in sampled_indices]

    return out_particles


def fixed_lag_stitching(graph, particles, weights, lag):
    """
    Resamples only elements of particles after a certain time - defined by the lag parameter.
    :param particles: MMParticles object (from inference.smc)
        trajectories generated
    :param weights: list-like, length = n
        weights at latest observation time
    :param lag: int
        lag parameter
        trajectories before this will be fixed
    :return: MMParticles object
        unweighted collection of trajectories post resampling + stitching
    """
    # Check weights are normalised
    weights_sum = sum(weights)
    if weights_sum != 1:
        weights /= weights_sum

    # Extract basic quantities
    observation_times = particles.observation_times
    m = len(observation_times)
    n = particles.n

    # If not reached lag yet do standard resampling
    if m <= lag:
        particles.ess = np.append(particles.ess, np.atleast_2d(np.ones(n) / sum(weights**2)), axis=1)
        return multinomial(particles, weights)

    # Largest time not to be resampled
    max_fixed_time = observation_times[m - lag - 1]

    # Smallest time to be resampled
    min_resample_time = observation_times[m - lag]

    # Initiate output
    out_particles = particles.copy()

    # Initiate ESS
    ess_track = np.zeros(n)

    # Iterate through particles
    for j in range(n):
        # Extract fixed particle
        max_fixed_time_index = np.where(out_particles[j][:, 0] == max_fixed_time)[0][0]
        fixed_particle = out_particles[j][:(max_fixed_time_index + 1)]
        last_edge_fixed = fixed_particle[-1]
        last_edge_fixed_geom = get_geometry(graph, last_edge_fixed[1:4])
        last_edge_fixed_length = last_edge_fixed_geom.length

        # Possible particles to be resampled placeholder
        newer_particles_adjusted = [None] * n

        # Initially set all resample weights to 0
        res_weights = np.zeros(n)

        for k in range(n):
            new_particle = particles[k][particles[k][:, 0] >= max_fixed_time].copy()

            # Check both particles start from same edge
            if np.array_equal(last_edge_fixed[1:4], new_particle[0, 1:4]):
                # Check that new edge overtakes fixed edge. i.e. distance isn't negative
                if np.array_equal(last_edge_fixed[1:4], new_particle[1, 1:4]) and \
                        new_particle[1, 4] < last_edge_fixed[4]:
                    continue

                # Calculate distance modification
                first_distance_j_to_k = (new_particle[1, 4] - last_edge_fixed[4]) * last_edge_fixed_length
                first_distance_k = new_particle[1, 6]

                change_dist = first_distance_j_to_k - first_distance_k

                new_particle[new_particle[:, 0] <= min_resample_time, 6] += change_dist

                # Store adjusted particle
                newer_particles_adjusted[k] = new_particle[1:]

                # Calculate adjusted weight
                res_weights = weights[k] \
                    * distance_prior(new_particle[new_particle[:, 0] <= min_resample_time][-1, 6]) \
                    / distance_prior(particles[k][particles[k][:, 0] <= min_resample_time][-1, 6])

        # Normalise adjusted resample weights
        res_weights /= sum(res_weights)

        # If only particle on fixed edge resample full trajectory
        if max(res_weights) == 1 or max(res_weights) == 0:
            out_particles[j] = particles[np.random.choice(n, 1, p=weights)[0]]

        # Otherwise fixed-lag resample and stitch
        else:
            # Resample index
            res_index = np.random.choice(n, 1, p=res_weights)[0]

            # Update output
            out_particles[j] = np.append(fixed_particle, newer_particles_adjusted[res_index], axis=0)

            # Track ESS
            ess_track[j] = 1 / sum(res_weights**2)

    # Append tracked ESS
    out_particles.ess = np.append(particles.ess, np.atleast_2d(ess_track), axis=1)

    return out_particles


